"""Subir un estudio: original cifrado → texto → limpieza de datos personales → IA → verificación.

Nada llega a `observation` sin revisión humana: la extracción queda en `extraction_row` hasta que
alguien la confirme (ver `confirm_review`).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime

from ..extract import Row, convert_ref, extract_document
from ..normalize import ranges, terminology
from ..privacy import Anonymizer
from ..providers import Router
from .vault import Vault

MAX_BYTES = 25 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS person_alias (
  person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name      TEXT NOT NULL                          -- como aparece en estudios: "ORTIZ HARO CARLOS"
);
CREATE TABLE IF NOT EXISTS extraction (
  document_id   INTEGER PRIMARY KEY REFERENCES document(id) ON DELETE CASCADE,
  sent_text     TEXT NOT NULL,                     -- lo que vio la IA (ya sin datos personales)
  redactions    TEXT NOT NULL,                     -- JSON: tipo -> cuántos se quitaron
  provider      TEXT, model TEXT, cost_usd REAL,
  collected_on  TEXT
);
CREATE TABLE IF NOT EXISTS extraction_row (
  id            INTEGER PRIMARY KEY,
  document_id   INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  analyte_key   TEXT, printed_name TEXT NOT NULL,
  value_num     REAL, value_text TEXT, unit TEXT,
  value_printed TEXT NOT NULL, unit_printed TEXT,
  ref_low REAL, ref_high REAL, ref_printed TEXT,
  status TEXT, method TEXT, section TEXT,
  evidence      TEXT NOT NULL,
  problems      TEXT NOT NULL,                     -- JSON: motivos para revisar con cuidado
  converted     INTEGER NOT NULL DEFAULT 0
);
"""


class IngestError(Exception):
    def __init__(self, status: int, message: str, document_id: int | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.document_id = status, message, document_id


def pdf_text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages)


def person_names(db: sqlite3.Connection, person: sqlite3.Row) -> list[str]:
    aliases = [
        r["name"] for r in db.execute("SELECT name FROM person_alias WHERE person_id = ?", (person["id"],))
    ]
    return [person["display_name"], *aliases]


@dataclass
class Ingested:
    document_id: int
    rows: int


def ingest_pdf(
    db: sqlite3.Connection,
    vault: Vault,
    router: Router,
    person: sqlite3.Row,
    filename: str,
    data: bytes,
    today: date | None = None,
) -> Ingested:
    if len(data) > MAX_BYTES:
        raise IngestError(413, "El archivo pesa más de 25 MB.")
    if not data.startswith(b"%PDF"):
        raise IngestError(415, "Por ahora solo se aceptan PDF.")
    sha = hashlib.sha256(data).hexdigest()
    dup = db.execute(
        "SELECT id FROM document WHERE person_id = ? AND file_sha256 = ?", (person["id"], sha)
    ).fetchone()
    if dup:
        raise IngestError(409, "Este estudio ya estaba cargado.", dup["id"])
    try:
        text = pdf_text(data)
    except Exception:
        raise IngestError(422, "No se pudo leer el PDF.") from None
    if len(re.sub(r"\s", "", text)) < 40:
        raise IngestError(
            422, "El PDF parece un escaneo sin texto. Los escaneos llegan en una fase posterior."
        )

    outcome = extract_document(
        text, router, anonymizer=Anonymizer(person_names(db, person)), reference_date=today or date.today()
    )
    stored = vault.put(data)
    title = re.sub(r"\.pdf$", "", filename, flags=re.I).strip() or "Estudio"
    cur = db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256) "
        "VALUES(?, 'laboratorio', ?, ?, ?, ?)",
        (person["id"], title, outcome.collected_on, stored, sha),
    )
    _store_extraction(db, cur.lastrowid, person["id"], outcome)
    return Ingested(cur.lastrowid, len(outcome.rows))


def reread(
    db: sqlite3.Connection,
    vault: Vault,
    router: Router,
    doc_id: int,
    person: sqlite3.Row,
    today: date | None = None,
) -> Ingested:
    """Vuelve a leer un estudio pendiente (catálogo mejorado o lector distinto) sin volver a subirlo."""
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if doc["review_state"] != "pendiente":
        raise IngestError(409, "Solo se puede volver a leer un estudio que aún no revisas.")
    outcome = extract_document(
        pdf_text(vault.get(doc["file_path"])),
        router,
        anonymizer=Anonymizer(person_names(db, person)),
        reference_date=today or date.today(),
    )
    db.execute("DELETE FROM extraction_row WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM extraction WHERE document_id = ?", (doc_id,))
    db.execute("UPDATE document SET collected_on = ? WHERE id = ?", (outcome.collected_on, doc_id))
    _store_extraction(db, doc_id, person["id"], outcome)
    return Ingested(doc_id, len(outcome.rows))


def _store_extraction(db: sqlite3.Connection, doc_id: int, person_id: int, outcome) -> None:
    llm = outcome.llm
    db.execute(
        "INSERT INTO extraction(document_id, sent_text, redactions, provider, model, cost_usd, collected_on) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            doc_id,
            outcome.sent_text,
            json.dumps(outcome.redactions),
            llm.provider,
            llm.model,
            llm.cost_usd,
            outcome.collected_on,
        ),
    )
    db.execute(
        "INSERT INTO ai_call(task, provider, model, input_tokens, output_tokens, cost_usd, request_id, "
        "person_id) VALUES('extract',?,?,?,?,?,?,?)",
        (
            llm.provider,
            llm.model,
            llm.input_tokens,
            llm.output_tokens,
            llm.cost_usd,
            llm.request_id,
            person_id,
        ),
    )
    for r in outcome.rows:
        _save_row(db, doc_id, r)


def _save_row(db: sqlite3.Connection, doc_id: int, r: Row) -> None:
    db.execute(
        "INSERT INTO extraction_row(document_id, analyte_key, printed_name, value_num, value_text, unit, "
        "value_printed, unit_printed, ref_low, ref_high, ref_printed, status, method, section, evidence, "
        "problems, converted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc_id,
            r.key,
            r.printed_name,
            r.value,
            r.value_label,
            r.unit,
            r.value_text,
            r.unit_text,
            r.ref_low,
            r.ref_high,
            r.ref_text,
            r.status,
            r.method,
            r.section,
            r.evidence,
            json.dumps(r.problems),
            int(r.converted),
        ),
    )


def review_payload(db: sqlite3.Connection, doc_id: int) -> dict:
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    ex = db.execute("SELECT * FROM extraction WHERE document_id = ?", (doc_id,)).fetchone()
    rows = []
    for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ? ORDER BY id", (doc_id,)):
        a = terminology.BY_KEY.get(r["analyte_key"] or "")
        rows.append(
            {
                **dict(r),
                "name": a.name if a else None,
                "problems": json.loads(r["problems"]),
                "needs_attention": bool(json.loads(r["problems"])) or bool(r["converted"]),
            }
        )
    return {
        "document": {
            k: doc[k] for k in ("id", "person_id", "title", "collected_on", "review_state", "uploaded_at")
        },
        "ai_saw": ex["sent_text"] if ex else None,
        "redactions": json.loads(ex["redactions"]) if ex else {},
        "rows": rows,
    }


def confirm_review(
    db: sqlite3.Connection, doc_id: int, reviewer_id: int, collected_on: str, decisions: list[dict]
) -> int:
    """Guarda en `observation` solo las filas aceptadas (con las correcciones del revisor)."""
    date.fromisoformat(collected_on)
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if doc["review_state"] != "pendiente":
        raise IngestError(409, "Este estudio ya fue revisado.")
    rows = {r["id"]: r for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ?", (doc_id,))}
    now = datetime.now().isoformat(timespec="seconds")
    saved = 0
    for d in decisions:
        r = rows.get(d.get("row_id"))
        if r is None:
            raise IngestError(422, "Fila desconocida.")
        if not d.get("accept"):
            continue
        key = d.get("analyte_key") or r["analyte_key"]
        analyte = terminology.BY_KEY.get(key or "")
        if analyte is None:
            raise IngestError(422, f"Falta indicar qué análisis es «{r['printed_name']}».")
        value_num = d.get("value_num", r["value_num"])
        value_text = d.get("value_text", r["value_text"])
        if value_num is None and not value_text:
            raise IngestError(422, f"«{r['printed_name']}» no tiene valor.")
        ref = ranges.parse_ref_full(r["ref_printed"])
        if value_num is not None:
            ref = convert_ref(key, ref, r["unit_printed"], analyte.unit)  # mismo criterio que el valor
            status = ranges.classify_ref(value_num, ref)
        else:
            status = ranges.classify_text(value_text, r["ref_printed"])
        db.execute(
            "INSERT INTO observation(person_id, document_id, analyte_key, loinc, printed_name, value_num, "
            "value_text, unit, value_printed, unit_printed, ref_low, ref_high, ref_printed, method, status, "
            "collected_on, confirmed_by, confirmed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc["person_id"],
                doc_id,
                key,
                analyte.loinc or None,
                r["printed_name"],
                value_num,
                value_text,
                d.get("unit", r["unit"]) or analyte.unit,
                r["value_printed"],
                r["unit_printed"],
                ref.low,
                ref.high,
                r["ref_printed"],
                r["method"],
                status,
                collected_on,
                reviewer_id,
                now,
            ),
        )
        saved += 1
    db.execute(
        "UPDATE document SET review_state = 'revisada', collected_on = ? WHERE id = ?", (collected_on, doc_id)
    )
    return saved


# --- Original junto a la revisión: páginas como imagen y ubicación de cada resultado ---


def _norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def page_layout(data: bytes, rows: list[sqlite3.Row]) -> dict:
    """Tamaño de cada página y, por fila extraída, el renglón del PDF donde aparece (en puntos)."""
    import pdfplumber

    pages, lines = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            pages.append({"n": n, "width": float(page.width), "height": float(page.height)})
            for ln in page.extract_text_lines(strip=True):
                lines.append((n, _norm_line(ln["text"]), ln))
    boxes: dict[int, dict] = {}
    for r in rows:
        # Si el valor ocupa dos renglones ("Ligera" / "Aspecto turbidez Claro"), se ubica el del nombre.
        ev, name, val = (
            _norm_line(r["evidence"].splitlines()[-1]),
            _norm_line(r["printed_name"]),
            _norm_line(r["value_printed"].split()[-1] if r["value_printed"].strip() else ""),
        )
        hit = next((x for x in lines if x[1] == ev), None) or next(
            (x for x in lines if ev and ev in x[1]), None
        )
        hit = hit or next((x for x in lines if x[1].startswith(name) and f" {val}" in x[1]), None)
        if hit:
            n, _, ln = hit
            boxes[r["id"]] = {
                "page": n,
                "x0": ln["x0"],
                "top": ln["top"],
                "x1": ln["x1"],
                "bottom": ln["bottom"],
            }
    return {"pages": pages, "boxes": boxes}


# PDFium (lo que dibuja las páginas) no admite varios hilos a la vez: sin este candado, pedir
# varias páginas en paralelo tumba el proceso completo (segmentation fault).
_PDFIUM = threading.Lock()


def render_page(data: bytes, n: int, resolution: int = 110) -> bytes:
    import pdfplumber

    with _PDFIUM, pdfplumber.open(io.BytesIO(data)) as pdf:
        if not 1 <= n <= len(pdf.pages):
            raise IngestError(404, "Página no encontrada.")
        buf = io.BytesIO()
        pdf.pages[n - 1].to_image(resolution=resolution).original.save(buf, "PNG")
        return buf.getvalue()


def repair_references(db: sqlite3.Connection) -> int:
    """Recalcula rango y estado de lo ya confirmado a partir de lo impreso (idempotente).

    Corrige resultados guardados antes de que el rango se convirtiera junto con el valor
    (p. ej. PCR: valor en mg/L con el rango "< 0.5" que era mg/dL). Devuelve cuántos cambió.
    """
    changed = 0
    rows = db.execute(
        "SELECT id, analyte_key, value_num, unit, unit_printed, ref_printed, ref_low, ref_high, status "
        "FROM observation WHERE value_num IS NOT NULL"  # incluye los que no traían rango: sin estado
    ).fetchall()
    for r in rows:
        analyte = terminology.BY_KEY.get(r["analyte_key"])
        if analyte is None:
            continue
        ref = convert_ref(
            r["analyte_key"], ranges.parse_ref_full(r["ref_printed"]), r["unit_printed"], analyte.unit
        )
        status = ranges.classify_ref(r["value_num"], ref)
        if (ref.low, ref.high, status) != (r["ref_low"], r["ref_high"], r["status"]):
            db.execute(
                "UPDATE observation SET ref_low = ?, ref_high = ?, status = ? WHERE id = ?",
                (ref.low, ref.high, status, r["id"]),
            )
            changed += 1
    return changed
