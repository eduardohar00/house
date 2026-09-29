"""Nombres claros para los documentos y estudios, puestos por Claude.

Los archivos suelen llamarse «2026-02-06 - Rx de Abdomen (1).pdf». Claude propone un nombre corto y limpio
(«Radiografía de abdomen») viendo solo el nombre del archivo, el tipo, la fecha y los nombres de los estudios
o grupos de análisis; nunca el contenido del documento. El nombre original del archivo se conserva siempre y
lo que la persona renombra a mano no se vuelve a cambiar.
"""

from __future__ import annotations

import json
import re
import sqlite3

from ..normalize import terminology
from ..providers import LLMRequest, Router

CHUNK = 25
GROUPS_ES = {
    "glucosa": "glucosa",
    "lipidos": "lípidos",
    "higado": "hígado",
    "pancreas": "páncreas",
    "coagulacion": "coagulación",
    "rinon": "riñón",
    "electrolitos": "electrolitos",
    "sangre": "biometría y hierro",
    "diferencial": "glóbulos blancos",
    "tiroides": "tiroides",
    "vitaminas": "vitaminas",
    "inmunologia": "inmunología",
    "marcadores": "marcadores",
    "orina": "orina",
    "seminal": "espermiograma",
    "infecciosas": "infecciones",
    "heces": "heces",
    "otros": "otros",
}

SYSTEM = """Das nombres claros y cortos a documentos médicos. Recibes, por documento: el nombre del archivo
subido, el tipo, la fecha y (según el caso) los nombres de los estudios que contiene, los grupos de
análisis que trae o el diagnóstico de una receta. NO ves el contenido del documento.

REGLAS
1. «title»: nombre corto y claro del documento, en español, con mayúscula solo al inicio y con acentos
   («Perfil tiroideo», «Check-up general», «Radiografía de tórax AP y lateral», «Panendoscopia»).
2. NO incluyas la fecha (se muestra aparte), la extensión, números de copia como «(1)», ni nombres de
   personas o de médicos.
3. Máximo 60 caracteres. Si el nombre del archivo ya es claro, respétalo con mejor ortografía.
4. Para laboratorios sin nombre claro, describe por sus grupos («Química sanguínea, lípidos y tiroides»).
   Para recetas, di para qué o qué medicamentos («Receta de omeprazol y sucralfato», «Receta por gastritis»);
   nunca «Receta médica» a secas si hay medicamentos o diagnóstico.
5. «studies»: para cada estudio que se te dio (en el mismo orden y la misma cantidad), su nombre limpio
   con la misma regla; si no se dieron estudios, una lista vacía.
Responde solo con el JSON pedido."""

_DATE_LEAD = re.compile(r"^\s*(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})\s*[-–:·]*\s*")
_COPY = re.compile(r"\s*\(\d+\)\s*$")
_EXT = re.compile(r"\.(pdf|png|jpe?g)$", re.I)


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "title": {"type": "string"},
                        "studies": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "title", "studies"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def tidy(name: str, fallback: str) -> str:
    """Nombre propuesto, limpio: sin fecha, extensión ni «(1)», con mayúscula inicial y largo razonable."""
    t = " ".join((name or "").split())
    t = _EXT.sub("", t)
    t = _DATE_LEAD.sub("", t)
    t = _COPY.sub("", t).strip(" -–:·")[:80].strip()
    if len(t) < 2:
        return fallback
    return t[:1].upper() + t[1:]


def _studies(db: sqlite3.Connection, d: sqlite3.Row) -> list[dict]:
    if d["doc_type"] != "imagen":
        return []
    if d["review_state"] == "pendiente":
        rows = db.execute(
            "SELECT position AS k, data FROM imaging_draft WHERE document_id = ? ORDER BY position",
            (d["id"],),
        ).fetchall()
        return [{"key": r["k"], "name": json.loads(r["data"]).get("study_name") or ""} for r in rows]
    rows = db.execute(
        "SELECT id AS k, study_name, name_source FROM imaging_study WHERE document_id = ? ORDER BY id",
        (d["id"],),
    ).fetchall()
    return [
        {"key": r["k"], "name": r["study_name"] or "", "manual": r["name_source"] == "manual"} for r in rows
    ]


def _topics(db: sqlite3.Connection, d: sqlite3.Row) -> list[str]:
    if d["doc_type"] == "receta":
        row = db.execute("SELECT data FROM prescription_draft WHERE document_id = ?", (d["id"],)).fetchone()
        if row:  # receta pendiente de revisar
            draft = json.loads(row["data"])
            meds = [m["name"] for m in draft.get("medications", [])[:3]]
            dx = draft.get("diagnosis")
        else:  # ya revisada: lo que se guardó de ella
            found = db.execute(
                "SELECT name, reason FROM medication WHERE document_id = ? ORDER BY id LIMIT 3", (d["id"],)
            ).fetchall()
            meds = [m["name"] for m in found]
            dx = next((m["reason"] for m in found if m["reason"]), None)
        return ([f"medicamentos: {', '.join(meds)}"] if meds else []) + ([f"diagnóstico: {dx}"] if dx else [])
    if d["doc_type"] != "laboratorio":
        return []
    table = "observation" if d["review_state"] == "revisada" else "extraction_row"
    keys = [
        r["k"] for r in db.execute(f"SELECT analyte_key AS k FROM {table} WHERE document_id = ?", (d["id"],))
    ]  # noqa: S608
    counts: dict[str, int] = {}
    for k in keys:
        a = terminology.BY_KEY.get(k or "")
        if a:
            counts[a.group] = counts.get(a.group, 0) + 1
    top = sorted(counts, key=lambda g: -counts[g])[:6]
    return [GROUPS_ES.get(g, g) for g in top]


def auto_name(
    db: sqlite3.Connection, router: Router, person_id: int, doc_ids: list[int] | None = None
) -> dict:
    """Pone nombre claro a los documentos que nadie ha renombrado. Devuelve cuántos cambió."""
    where = "person_id = ? AND name_source IS NULL AND review_state != 'descartada'"
    args: list = [person_id]
    if doc_ids is not None:
        if not doc_ids:
            return {"renamed": 0, "checked": 0}
        where += f" AND id IN ({','.join('?' * len(doc_ids))})"  # noqa: S608
        args += doc_ids
    docs = db.execute(f"SELECT * FROM document WHERE {where} ORDER BY id", args).fetchall()  # noqa: S608
    renamed = 0
    for i in range(0, len(docs), CHUNK):
        chunk = docs[i : i + CHUNK]
        items = []
        for d in chunk:
            studies = _studies(db, d)
            items.append(
                {"id": d["id"], "filename": d["filename"] or d["title"], "type": d["doc_type"],
                 "date": d["collected_on"], "studies": [s["name"] for s in studies], "topics": _topics(db, d)}
            )  # fmt: skip
        req = LLMRequest(
            task="interpret",
            system=SYSTEM,
            user="Documentos:\n" + json.dumps(items, ensure_ascii=False),
            schema=schema(),
            max_tokens=4000,
        )
        answers = {a["id"]: a for a in router.complete_json(req).data.get("items", []) if isinstance(a, dict)}
        for d in chunk:
            a = answers.get(d["id"])
            if not a:
                continue
            title = tidy(a.get("title", ""), d["title"])
            db.execute(
                "UPDATE document SET filename = COALESCE(filename, title), title = ?, name_source = 'ia' "
                "WHERE id = ? AND name_source IS NULL",
                (title, d["id"]),
            )
            _apply_studies(db, d, a.get("studies") or [])
            renamed += 1
    return {"renamed": renamed, "checked": len(docs)}


def _apply_studies(db: sqlite3.Connection, d: sqlite3.Row, names: list[str]) -> None:
    studies = _studies(db, d)
    if not studies or len(names) != len(studies):
        return  # solo si Claude contestó uno por estudio, en el mismo orden
    for s, new in zip(studies, names, strict=True):
        clean = tidy(new, s["name"])
        if d["review_state"] == "pendiente":
            row = db.execute(
                "SELECT data FROM imaging_draft WHERE document_id = ? AND position = ?", (d["id"], s["key"])
            ).fetchone()
            data = json.loads(row["data"])
            data["study_name"] = clean
            db.execute(
                "UPDATE imaging_draft SET data = ? WHERE document_id = ? AND position = ?",
                (json.dumps(data, ensure_ascii=False), d["id"], s["key"]),
            )
        elif not s.get("manual"):
            db.execute(
                "UPDATE imaging_study SET study_name = ?, name_source = 'ia' WHERE id = ?", (clean, s["key"])
            )
