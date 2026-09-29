"""Expediente clínico: alergias, problemas de salud, medicamentos, antecedentes, cirugías, vacunas y
consultas, todo escrito por la persona, más el historial cronológico que junta también los estudios.

Cada dato es de captura manual. La ausencia de datos no es lo mismo que "no hay": por eso una sección
vacía se puede confirmar explícitamente ("sin alergias conocidas").
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..imaging import MODALITIES
from ..normalize import terminology

# Modalidades que son imagen; las demás (endoscopia, patología, ECG...) se muestran como "otro estudio".
IMAGING = {label for _, label in MODALITIES} - {"Electrocardiograma"}
PROBLEM_STATUSES = ("En control", "En tratamiento", "Seguimiento", "Resuelta")
NONE_SECTIONS = ("allergy", "medication", "problem", "procedure", "family")
ALLERGY_CATEGORIES = (
    "Medicamento",
    "Alimento",
    "Ambiental",
    "Otro",
)  # «sin alergias» aplica solo a medicamentos
OUT = ("low", "high", "abnormal")


class ClinicalError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


# ---------- Esquema y migración (bases anteriores) ----------

SCHEMA = """
CREATE TABLE IF NOT EXISTS vaccine (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name TEXT NOT NULL, given_on TEXT NOT NULL, dose_label TEXT, place TEXT, notes TEXT,
  brand TEXT, lot TEXT
);
CREATE TABLE IF NOT EXISTS consultation (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  occurred_on TEXT NOT NULL, reason TEXT NOT NULL, doctor TEXT, specialty TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS symptom (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  occurred_on TEXT NOT NULL, what TEXT NOT NULL, related TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS supplement (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name TEXT NOT NULL, dose TEXT, brand TEXT, reason TEXT, since_year TEXT, until_year TEXT,
  active INTEGER NOT NULL DEFAULT 1, notes TEXT
);
CREATE TABLE IF NOT EXISTS problem_link (
  id INTEGER PRIMARY KEY,
  problem_id INTEGER NOT NULL REFERENCES problem(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('document', 'imaging', 'analyte', 'medication', 'procedure')),
  ref TEXT NOT NULL,                              -- id del documento, informe, tratamiento o cirugía; o clave
  UNIQUE (problem_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS clinical_none (
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  section   TEXT NOT NULL,                       -- "confirmado: no hay" (p. ej. sin alergias conocidas)
  PRIMARY KEY (person_id, section)
);
"""

_EXTRA_COLUMNS = {
    "medication": {
        "prescriber": "TEXT",
        "until_year": "TEXT",
        "notes": "TEXT",
        "document_id": "INTEGER",
        "active_ingredient": "TEXT",
        "brand": "TEXT",
        "bad_reaction": "TEXT",
    },
    "problem": {"notes": "TEXT"},
    "allergy": {"notes": "TEXT", "category": "TEXT"},
    "procedure_history": {"notes": "TEXT"},
    "vaccine": {"brand": "TEXT", "lot": "TEXT"},
}


def _upgrade_problem_link(db) -> None:
    """Bases anteriores: la tabla solo admitía laboratorios, informes y análisis; ahora también más."""
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'problem_link'"
    ).fetchone()
    if row is None or "'medication'" in row["sql"]:
        return
    db.execute("ALTER TABLE problem_link RENAME TO problem_link_old")
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO problem_link(id, problem_id, kind, ref) "
        "SELECT id, problem_id, kind, ref FROM problem_link_old"
    )
    db.execute("DROP TABLE problem_link_old")


def migrate(db) -> None:
    _upgrade_problem_link(db)
    db.executescript(SCHEMA)
    had_brand = "brand" in {r["name"] for r in db.execute("PRAGMA table_info(vaccine)")}
    for table, cols in _EXTRA_COLUMNS.items():
        have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - tablas fijas
        for col, typ in cols.items():
            if have and col not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")  # noqa: S608
    if not had_brand:
        _split_vaccine_brands(db)


def _split_vaccine_brands(db) -> None:
    """Una sola vez, al agregar la marca: «COVID-19 Pfizer» pasa a vacuna «COVID-19» con marca «Pfizer»."""
    bases = sorted(SUGGESTIONS["vaccine"], key=len, reverse=True)
    for v in db.execute("SELECT id, name FROM vaccine").fetchall():
        for base in bases:
            rest = v["name"][len(base) :].strip()
            if v["name"].lower().startswith(base.lower() + " ") and rest:
                db.execute("UPDATE vaccine SET name = ?, brand = ? WHERE id = ?", (base, rest, v["id"]))
                break


# ---------- Tipos de dato ----------


@dataclass(frozen=True)
class Field:
    kind: str  # text | year | date | enum | bool
    required: bool = False
    max: int = 200
    options: tuple[str, ...] = ()
    default: Any = None


DOSE_OPTIONS = ("Dosis única", "Primera dosis", "Segunda dosis", "Tercera dosis", "Refuerzo", "Anual")

SPECS: dict[str, tuple[str, dict[str, Field]]] = {
    "allergy": ("allergy", {
        "substance": Field("text", True), "reaction": Field("text"), "notes": Field("text", max=1000),
        "category": Field("enum", options=ALLERGY_CATEGORIES),
    }),
    "problem": ("problem", {
        "name": Field("text", True), "status": Field("enum", True, options=PROBLEM_STATUSES),
        "since_year": Field("year"), "notes": Field("text", max=4000),
    }),
    "medication": ("medication", {
        "name": Field("text"), "active_ingredient": Field("text"), "brand": Field("text"),
        "dose": Field("text"), "reason": Field("text"),
        "prescriber": Field("text"), "since_year": Field("year"), "until_year": Field("year"),
        "active": Field("bool", default=True), "notes": Field("text", max=1000),
        "bad_reaction": Field("text", max=300),
    }),
    "symptom": ("symptom", {
        "occurred_on": Field("date", True), "what": Field("text", True, max=300),
        "related": Field("text", max=200), "notes": Field("text", max=1000),
    }),
    "supplement": ("supplement", {
        "name": Field("text", True), "dose": Field("text"), "brand": Field("text"), "reason": Field("text"),
        "since_year": Field("year"), "until_year": Field("year"), "active": Field("bool", default=True),
        "notes": Field("text", max=1000),
    }),
    "family": ("family_history", {"relative": Field("text", True), "condition": Field("text", True)}),
    "procedure": ("procedure_history", {
        "name": Field("text", True), "year": Field("year"), "notes": Field("text", max=1000),
    }),
    "vaccine": ("vaccine", {
        "name": Field("text", True), "given_on": Field("date", True), "dose_label": Field("text"),
        "brand": Field("text"), "lot": Field("text"), "place": Field("text"),
        "notes": Field("text", max=1000),
    }),
    "consultation": ("consultation", {
        "occurred_on": Field("date", True), "reason": Field("text", True), "doctor": Field("text"),
        "specialty": Field("text"), "notes": Field("text", max=1000),
    }),
}  # fmt: skip

_LABELS = {
    "substance": "La sustancia", "name": "El nombre", "status": "El estado", "relative": "El parentesco",
    "condition": "La condición", "what": "Lo que notaste", "given_on": "La fecha",
    "occurred_on": "La fecha", "reason": "El motivo",
}  # fmt: skip


def _clean_field(col: str, f: Field, raw: Any) -> Any:
    label = _LABELS.get(col, "Este dato")
    if f.kind == "bool":
        return int(bool(raw)) if raw is not None else int(bool(f.default))
    text = "" if raw is None else str(raw).strip()
    if not text:
        if f.required:
            raise ClinicalError(422, f"{label} es obligatorio.")
        return None
    if len(text) > f.max:
        raise ClinicalError(422, f"{label} es demasiado largo (máximo {f.max} caracteres).")
    if f.kind == "year":
        if not re.fullmatch(r"\d{4}", text) or not 1900 <= int(text) <= date.today().year:
            raise ClinicalError(422, "El año debe tener 4 cifras y no puede ser futuro.")
    elif f.kind == "date":
        try:
            when = date.fromisoformat(text)
        except ValueError:
            raise ClinicalError(422, "La fecha no es válida (usa AAAA-MM-DD).") from None
        if when > date.today() or when.year < 1900:
            raise ClinicalError(422, "La fecha no puede ser futura.")
    elif f.kind == "enum" and text not in f.options:
        raise ClinicalError(422, f"{label} no es válido.")
    return text


def clean(kind: str, data: dict) -> dict[str, Any]:
    if kind not in SPECS:
        raise ClinicalError(404, "Tipo de dato desconocido.")
    fields = SPECS[kind][1]
    out = {col: _clean_field(col, f, data.get(col)) for col, f in fields.items()}
    if kind == "medication":
        # `name` es el nombre con el que se identifica: la sustancia activa o, si no se conoce, la marca.
        out["name"] = out["active_ingredient"] or out["brand"] or out["name"]
        if not out["name"]:
            raise ClinicalError(422, "Escribe la sustancia activa o el nombre comercial.")
    if (
        kind in ("medication", "supplement")
        and out["since_year"]
        and out["until_year"]
        and out["until_year"] < out["since_year"]
    ):
        raise ClinicalError(422, "El año en que se suspendió no puede ser anterior al de inicio.")
    return out


# ---------- Operaciones ----------


def _breaks_none(kind: str, values: dict) -> bool:
    """Un dato nuevo invalida «confirmado: no hay», salvo medicamento suspendido o alergia ambiental."""
    if kind not in NONE_SECTIONS:
        return False
    if kind == "medication":
        return bool(values["active"])
    if kind == "allergy":
        return (values.get("category") or "Medicamento") == "Medicamento"
    return True


def add(db, person_id: int, kind: str, data: dict) -> int:
    values = clean(kind, data)
    table = SPECS[kind][0]
    cols = ", ".join(["person_id", *values])
    marks = ", ".join("?" * (len(values) + 1))
    cur = db.execute(f"INSERT INTO {table}({cols}) VALUES({marks})", (person_id, *values.values()))  # noqa: S608
    # Ya hay datos: deja de valer "confirmado: no hay". Un medicamento suspendido no cuenta como actual.
    if _breaks_none(kind, values):
        db.execute("DELETE FROM clinical_none WHERE person_id = ? AND section = ?", (person_id, kind))
    return cur.lastrowid


def update(db, person_id: int, kind: str, item_id: int, data: dict) -> None:
    values = clean(kind, data)
    table = SPECS[kind][0]
    sets = ", ".join(f"{c} = ?" for c in values)
    cur = db.execute(
        f"UPDATE {table} SET {sets} WHERE id = ? AND person_id = ?",  # noqa: S608
        (*values.values(), item_id, person_id),
    )
    if cur.rowcount == 0:
        raise ClinicalError(404, "No encontré ese dato en este perfil.")
    if _breaks_none(kind, values):
        db.execute("DELETE FROM clinical_none WHERE person_id = ? AND section = ?", (person_id, kind))


def remove(db, person_id: int, kind: str, item_id: int) -> None:
    if kind not in SPECS:
        raise ClinicalError(404, "Tipo de dato desconocido.")
    cur = db.execute(f"DELETE FROM {SPECS[kind][0]} WHERE id = ? AND person_id = ?", (item_id, person_id))  # noqa: S608
    if cur.rowcount == 0:
        raise ClinicalError(404, "No encontré ese dato en este perfil.")


def set_none(db, person_id: int, section: str, confirmed: bool) -> None:
    if section not in NONE_SECTIONS:
        raise ClinicalError(404, "Sección desconocida.")
    if not confirmed:
        db.execute("DELETE FROM clinical_none WHERE person_id = ? AND section = ?", (person_id, section))
        return
    table = SPECS[section][0]
    drug = " AND COALESCE(category, 'Medicamento') = 'Medicamento'"
    only_current = {"medication": " AND active = 1", "allergy": drug}.get(section, "")
    if db.execute(f"SELECT 1 FROM {table} WHERE person_id = ?{only_current}", (person_id,)).fetchone():  # noqa: S608
        raise ClinicalError(409, "Ya hay datos en esta sección; no se puede confirmar que no hay.")
    db.execute("INSERT OR IGNORE INTO clinical_none(person_id, section) VALUES(?, ?)", (person_id, section))


# ---------- Padecimientos ligados a estudios ----------

LINK_KINDS = ("document", "imaging", "analyte", "medication", "procedure")


def link_candidates(db, person_id: int) -> dict:
    """Lo que se puede ligar a un padecimiento: laboratorios, informes y análisis que tienen resultados."""
    documents = [
        {"ref": str(r["id"]), "title": r["title"], "date": r["collected_on"]}
        for r in db.execute(
            "SELECT id, title, collected_on FROM document WHERE person_id = ? AND doc_type = 'laboratorio' "
            "AND review_state = 'revisada' ORDER BY collected_on DESC",
            (person_id,),
        )
    ]
    imaging = [
        {"ref": str(r["id"]), "title": r["study_name"] or r["modality"], "date": r["performed_on"]}
        for r in db.execute(
            "SELECT id, study_name, modality, performed_on FROM imaging_study WHERE person_id = ? "
            "ORDER BY performed_on DESC, id",
            (person_id,),
        )
    ]
    analytes = []
    for r in db.execute(
        "SELECT analyte_key, COUNT(*) AS n, MAX(collected_on) AS last FROM observation WHERE person_id = ? "
        "GROUP BY analyte_key",
        (person_id,),
    ):
        a = terminology.BY_KEY.get(r["analyte_key"])
        analytes.append({"ref": r["analyte_key"], "title": a.name if a else r["analyte_key"],
                         "date": r["last"], "results": r["n"]})  # fmt: skip
    analytes.sort(key=lambda a: _plain(a["title"]))
    medications = [
        {"ref": str(r["id"]), "title": _med_title(r), "reason": r["reason"], "active": bool(r["active"])}
        for r in db.execute(
            "SELECT id, name, dose, reason, active FROM medication WHERE person_id = ? "
            "ORDER BY active DESC, name COLLATE NOCASE",
            (person_id,),
        )
    ]
    procedures = [
        {"ref": str(r["id"]), "title": r["name"], "date": r["year"]}
        for r in db.execute(
            "SELECT id, name, year FROM procedure_history WHERE person_id = ? ORDER BY year DESC, name",
            (person_id,),
        )
    ]
    return {
        "documents": documents,
        "imaging": imaging,
        "analytes": analytes,
        "medications": medications,
        "procedures": procedures,
    }


def _med_title(r) -> str:
    return f"{r['name']} · {r['dose']}" if r["dose"] else r["name"]


def _link_target(db, person_id: int, kind: str, ref: str) -> dict | None:
    """Datos legibles de lo ligado; None si ya no existe (se borró el estudio)."""
    if kind == "document":
        r = db.execute(
            "SELECT id, title, collected_on FROM document "
            "WHERE id = ? AND person_id = ? AND doc_type = 'laboratorio'",
            (ref, person_id),
        ).fetchone()
        return {"title": r["title"], "date": r["collected_on"], "document_id": r["id"]} if r else None
    if kind == "imaging":
        r = db.execute(
            "SELECT id, study_name, modality, performed_on, document_id FROM imaging_study "
            "WHERE id = ? AND person_id = ?",
            (ref, person_id),
        ).fetchone()
        return (
            {
                "title": r["study_name"] or r["modality"],
                "date": r["performed_on"],
                "document_id": r["document_id"],
            }
            if r
            else None
        )
    if kind == "medication":
        r = db.execute(
            "SELECT name, dose, active, since_year, until_year FROM medication "
            "WHERE id = ? AND person_id = ?",
            (ref, person_id),
        ).fetchone()
        if r is None:
            return None
        years = f"desde {r['since_year']}" if r["since_year"] else ""
        if r["until_year"]:
            years += f" hasta {r['until_year']}"
        return {"title": r["name"], "date": None, "value": r["dose"], "extra": years.strip() or None,
                "active": bool(r["active"])}  # fmt: skip
    if kind == "procedure":
        r = db.execute(
            "SELECT name, year FROM procedure_history WHERE id = ? AND person_id = ?", (ref, person_id)
        ).fetchone()
        return {"title": r["name"], "date": None, "extra": r["year"]} if r else None
    last = db.execute(
        "SELECT value_num, value_text, unit, status, collected_on FROM observation "
        "WHERE person_id = ? AND analyte_key = ? ORDER BY collected_on DESC, id DESC LIMIT 1",
        (person_id, ref),
    ).fetchone()
    if last is None:
        return None
    a = terminology.BY_KEY.get(ref)
    value = (
        last["value_text"]
        if last["value_num"] is None
        else f"{last['value_num']:g} {last['unit'] or ''}".strip()
    )
    return {
        "title": a.name if a else ref,
        "date": last["collected_on"],
        "value": value,
        "status": last["status"],
    }


def problem_links(db, person_id: int) -> dict[int, list[dict]]:
    """Estudios ligados a cada padecimiento. Los que ya no existen se limpian."""
    out: dict[int, list[dict]] = {}
    for r in db.execute(
        "SELECT l.id, l.problem_id, l.kind, l.ref FROM problem_link l JOIN problem p ON p.id = l.problem_id "
        "WHERE p.person_id = ? ORDER BY l.id",
        (person_id,),
    ).fetchall():
        target = _link_target(db, person_id, r["kind"], r["ref"])
        if target is None:
            db.execute("DELETE FROM problem_link WHERE id = ?", (r["id"],))
            continue
        out.setdefault(r["problem_id"], []).append(
            {"id": r["id"], "kind": r["kind"], "ref": r["ref"], **target}
        )
    return out


def problems_of(db, person_id: int) -> dict[tuple[str, str], list[dict]]:
    """Al revés: (tipo, ref) -> padecimientos a los que está ligado."""
    out: dict[tuple[str, str], list[dict]] = {}
    for r in db.execute(
        "SELECT l.kind, l.ref, p.id, p.name FROM problem_link l JOIN problem p ON p.id = l.problem_id "
        "WHERE p.person_id = ? ORDER BY p.name COLLATE NOCASE",
        (person_id,),
    ):
        out.setdefault((r["kind"], r["ref"]), []).append({"id": r["id"], "name": r["name"]})
    return out


def add_link(db, person_id: int, problem_id: int, kind: str, ref: str) -> int:
    if kind not in LINK_KINDS:
        raise ClinicalError(422, "Tipo de estudio desconocido.")
    if not db.execute(
        "SELECT 1 FROM problem WHERE id = ? AND person_id = ?", (problem_id, person_id)
    ).fetchone():
        raise ClinicalError(404, "No encontré ese padecimiento en este perfil.")
    ref = str(ref)
    if _link_target(db, person_id, kind, ref) is None:
        raise ClinicalError(404, "Ese estudio no existe en este perfil.")
    db.execute(
        "INSERT OR IGNORE INTO problem_link(problem_id, kind, ref) VALUES(?,?,?)", (problem_id, kind, ref)
    )
    return db.execute(
        "SELECT id FROM problem_link WHERE problem_id = ? AND kind = ? AND ref = ?", (problem_id, kind, ref)
    ).fetchone()["id"]


def remove_link(db, person_id: int, problem_id: int, link_id: int) -> None:
    cur = db.execute(
        "DELETE FROM problem_link WHERE id = ? AND problem_id = ? AND problem_id IN "
        "(SELECT id FROM problem WHERE person_id = ?)",
        (link_id, problem_id, person_id),
    )
    if cur.rowcount == 0:
        raise ClinicalError(404, "No encontré esa relación.")


def _plain(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _rows(db, table: str, person_id: int, order: str) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(f"SELECT * FROM {table} WHERE person_id = ? ORDER BY {order}", (person_id,))
    ]  # noqa: S608


def timeline(db, person_id: int) -> list[dict]:
    """Consultas, estudios de laboratorio e imagen, vacunas y cirugías, del más reciente al más antiguo."""
    ev: list[dict] = []
    for c in _rows(db, "consultation", person_id, "occurred_on"):
        sub = " · ".join(x for x in (c["specialty"], c["doctor"]) if x)
        ev.append(
            {"kind": "consulta", "date": c["occurred_on"], "title": c["reason"], "subtitle": sub, "ref": None}
        )
    for s in _rows(db, "symptom", person_id, "occurred_on"):
        sub = " · ".join(x for x in (f"después de {s['related']}" if s["related"] else "", s["notes"]) if x)
        ev.append(
            {"kind": "sintoma", "date": s["occurred_on"], "title": s["what"], "subtitle": sub, "ref": None}
        )
    for v in _rows(db, "vaccine", person_id, "given_on"):
        sub = " · ".join(x for x in (v["dose_label"], v["brand"], v["place"]) if x)
        ev.append({"kind": "vacuna", "date": v["given_on"], "title": v["name"], "subtitle": sub, "ref": None})
    for p in _rows(db, "procedure_history", person_id, "id"):
        if p["year"]:
            ev.append({"kind": "cirugia", "date": f"{p['year']}-01-01", "approx": True, "title": p["name"],
                       "subtitle": "", "ref": None})  # fmt: skip
    for d in db.execute(
        "SELECT d.id, d.title, d.collected_on, GROUP_CONCAT(m.name, ', ') AS names, COUNT(m.id) AS n "
        "FROM document d LEFT JOIN medication m ON m.document_id = d.id "
        "WHERE d.person_id = ? AND d.doc_type = 'receta' AND d.review_state = 'revisada' "
        "AND d.collected_on IS NOT NULL GROUP BY d.id",
        (person_id,),
    ):
        ev.append({"kind": "receta", "date": d["collected_on"], "title": f"Receta · {d['n']} medicamento(s)",
                   "subtitle": d["names"] or "", "ref": {"type": "document", "id": d["id"]}})  # fmt: skip
    for d in db.execute(
        "SELECT d.id, d.collected_on, COUNT(m.id) AS n FROM body_scan b "
        "JOIN document d ON d.id = b.document_id LEFT JOIN measurement m ON m.document_id = d.id "
        "WHERE b.person_id = ? AND b.confirmed = 1 AND d.collected_on IS NOT NULL GROUP BY d.id",
        (person_id,),
    ):
        ref = {"type": "document", "id": d["id"]}
        ev.append(
            {"kind": "cuerpo", "date": d["collected_on"], "title": "Composición corporal",
             "subtitle": f"{d['n']} medidas del reporte", "ref": ref}
        )  # fmt: skip
    labs = db.execute(
        "SELECT d.id, d.title, d.collected_on, COUNT(o.id) AS n, "
        "COALESCE(SUM(o.status IN ('low','high','abnormal')), 0) AS n_out "
        "FROM document d LEFT JOIN observation o ON o.document_id = d.id "
        "WHERE d.person_id = ? AND d.doc_type = 'laboratorio' AND d.review_state = 'revisada' "
        "AND d.collected_on IS NOT NULL GROUP BY d.id",
        (person_id,),
    )
    for d in labs:
        sub = f"{d['n']} resultados" + (
            f", {d['n_out']} fuera de rango" if d["n_out"] else ", todos en rango"
        )
        ev.append({"kind": "laboratorio", "date": d["collected_on"], "title": d["title"], "subtitle": sub,
                   "ref": {"type": "document", "id": d["id"]}})  # fmt: skip
    for i in db.execute(
        "SELECT id, document_id, performed_on, study_name, modality, conclusion, flag FROM imaging_study "
        "WHERE person_id = ?",
        (person_id,),
    ):
        ref = {"type": "document", "id": i["document_id"]} if i["document_id"] else None
        title = i["study_name"] or i["modality"]
        ev.append(
            {
                "kind": "imagen" if i["modality"] in IMAGING else "estudio",
                "modality": i["modality"],
                "date": i["performed_on"],
                "title": title,
                "subtitle": i["conclusion"] or "",
                "flag": i["flag"],
                "ref": ref,
            }
        )
    return sorted(ev, key=lambda e: (e["date"], e["kind"]), reverse=True)


def overview(db, person_id: int) -> dict:
    meds = _rows(db, "medication", person_id, "active DESC, name COLLATE NOCASE")
    when = {  # fecha de la receta de la que salió cada medicamento
        r["id"]: r["collected_on"]
        for r in db.execute("SELECT id, collected_on FROM document WHERE person_id = ?", (person_id,))
    }
    for m in meds:
        m["prescribed_on"] = when.get(m["document_id"]) if m.get("document_id") else None
    names = [_plain(m["name"]) for m in meds if m["active"]]
    for m in meds:
        m["duplicate"] = bool(m["active"]) and names.count(_plain(m["name"])) > 1
    problems = _rows(db, "problem", person_id, "name COLLATE NOCASE")
    problems.sort(key=lambda p: p["status"] == "Resuelta")
    links = problem_links(db, person_id)
    for p in problems:
        p["links"] = links.get(p["id"], [])
    reverse = problems_of(db, person_id)
    for m in meds:
        m["problems"] = reverse.get(("medication", str(m["id"])), [])
    return {
        "allergies": _rows(db, "allergy", person_id, "category, substance COLLATE NOCASE"),
        "allergy_categories": list(ALLERGY_CATEGORIES),
        "problems": problems,
        "medications": meds,
        "supplements": _rows(db, "supplement", person_id, "active DESC, name COLLATE NOCASE"),
        "family": _rows(db, "family_history", person_id, "relative COLLATE NOCASE, condition COLLATE NOCASE"),
        "procedures": _rows(db, "procedure_history", person_id, "year DESC, name COLLATE NOCASE"),
        "vaccines": _rows(db, "vaccine", person_id, "given_on DESC"),
        "consultations": _rows(db, "consultation", person_id, "occurred_on DESC"),
        "symptoms": _rows(db, "symptom", person_id, "occurred_on DESC, id DESC"),
        "none": [
            r["section"]
            for r in db.execute("SELECT section FROM clinical_none WHERE person_id = ?", (person_id,))
        ],
        "timeline": timeline(db, person_id),
        "statuses": list(PROBLEM_STATUSES),
        "dose_options": list(DOSE_OPTIONS),
    }


# ---------- Sugerencias para autocompletar (solo nombres; no son consejo médico) ----------

SUGGESTIONS = {
    "supplement": [
        "Proteína (whey)",
        "Proteína vegetal",
        "Creatina monohidratada",
        "Omega 3",
        "Vitamina D3",
        "Vitamina C",
        "Vitamina B12",
        "Multivitamínico",
        "Magnesio",
        "Zinc",
        "Hierro",
        "Calcio",
        "Colágeno",
        "Probióticos",
        "Melatonina",
        "Cafeína",
        "BCAA",
        "Ashwagandha",
        "Ácido fólico",
        "Fibra (psyllium)",
        "Electrolitos",
        "Pre-entreno",
    ],
    "medication": [
        "Paracetamol",
        "Ibuprofeno",
        "Naproxeno",
        "Diclofenaco",
        "Ácido acetilsalicílico",
        "Omeprazol",
        "Pantoprazol",
        "Loratadina",
        "Cetirizina",
        "Metformina",
        "Glibenclamida",
        "Losartán",
        "Enalapril",
        "Captopril",
        "Amlodipino",
        "Hidroclorotiazida",
        "Atorvastatina",
        "Simvastatina",
        "Rosuvastatina",
        "Levotiroxina",
        "Amoxicilina",
        "Azitromicina",
        "Ciprofloxacino",
        "Clonazepam",
        "Sertralina",
        "Escitalopram",
        "Salbutamol",
        "Insulina glargina",
        "Vitamina D3",
        "Ácido fólico",
        "Sulfato ferroso",
        "Vitamina B12",
        "Complejo B",
        "Omega 3",
        "Calcio con vitamina D",
        "Tamsulosina",
        "Finasterida",
    ],
    "allergy": [
        "Penicilina",
        "Sulfonamidas (sulfas)",
        "Aspirina y antiinflamatorios",
        "Látex",
        "Polen",
        "Ácaros del polvo",
        "Cacahuate",
        "Mariscos",
        "Huevo",
        "Leche",
        "Nueces",
        "Yodo",
        "Picadura de abeja",
    ],
    "problem": [
        "Hipertensión arterial",
        "Diabetes tipo 2",
        "Prediabetes",
        "Dislipidemia",
        "Hipotiroidismo",
        "Gastritis",
        "Asma",
        "Rinitis alérgica",
        "Migraña",
        "Ansiedad",
        "Depresión",
        "Hígado graso",
        "Deficiencia de vitamina D",
        "Deficiencia de hierro",
        "Osteopenia",
    ],
    "procedure": [
        "Apendicectomía",
        "Colecistectomía",
        "Amigdalectomía",
        "Cesárea",
        "Hernioplastía",
        "Vasectomía",
        "Cirugía de rodilla",
        "Cirugía de columna",
        "Histerectomía",
    ],
    "vaccine": [
        "Influenza estacional",
        "COVID-19",
        "Tétanos (Td)",
        "Tdap",
        "Hepatitis B",
        "Hepatitis A",
        "VPH",
        "Neumococo",
        "Sarampión, rubéola y paperas (SRP)",
        "Varicela",
        "Herpes zóster",
        "Meningococo",
    ],
    "relative": [
        "Madre",
        "Padre",
        "Hermano o hermana",
        "Abuelo o abuela materna",
        "Abuelo o abuela paterna",
        "Tío o tía",
        "Hijo o hija",
    ],
}
