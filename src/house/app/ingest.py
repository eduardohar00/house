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
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from ..extract import Row, convert_ref, extract_document
from ..imaging import ImagingReport, looks_like_lab, parse_reports
from ..normalize import critical, ranges, terminology, units
from ..privacy import Anonymizer
from ..providers import Router
from . import ocr
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
  converted     INTEGER NOT NULL DEFAULT 0,
  qualifier     TEXT                               -- "<" o ">" impreso antes del valor
);
"""


IMAGING_SCHEMA = """
CREATE TABLE IF NOT EXISTS imaging_draft (
  id          INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL,
  data        TEXT NOT NULL                        -- informe leído, pendiente de revisar (JSON)
);
"""

IMAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS study_image (
  id           INTEGER PRIMARY KEY,
  person_id    INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  title        TEXT NOT NULL,
  performed_on TEXT,
  media_type   TEXT NOT NULL,
  file_path    TEXT NOT NULL,                      -- imagen cifrada, fuera de la base
  file_sha256  TEXT NOT NULL,
  uploaded_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (person_id, file_sha256)
);
"""

# Columnas que se agregaron a imaging_study después de la primera versión del esquema.
_IMAGING_COLUMNS = {
    "study_name": "TEXT", "technique": "TEXT", "indication": "TEXT", "findings": "TEXT", "prior": "TEXT",
    "conclusion": "TEXT", "suggestions": "TEXT", "radiologist": "TEXT", "site": "TEXT", "flag": "TEXT",
    "confirmed_by": "INTEGER", "confirmed_at": "TEXT",
}  # fmt: skip


def migrate_imaging(db: sqlite3.Connection) -> None:
    """Prepara bases creadas antes de los informes de imagen, sin tocar sus datos."""
    db.executescript(IMAGING_SCHEMA)
    db.executescript(IMAGE_SCHEMA)
    have = {r["name"] for r in db.execute("PRAGMA table_info(imaging_study)")}
    for col, typ in _IMAGING_COLUMNS.items():
        if col not in have:
            db.execute(f"ALTER TABLE imaging_study ADD COLUMN {col} {typ}")


def migrate_observation(db: sqlite3.Connection) -> None:
    """Agrega a `observation` la marca de resultado escrito a mano y el signo < / > (bases anteriores)."""
    have = {r["name"] for r in db.execute("PRAGMA table_info(observation)")}
    if have and "entered_manually" not in have:
        db.execute("ALTER TABLE observation ADD COLUMN entered_manually INTEGER NOT NULL DEFAULT 0")
    if have and "qualifier" not in have:
        db.execute("ALTER TABLE observation ADD COLUMN qualifier TEXT")
    have_rows = {r["name"] for r in db.execute("PRAGMA table_info(extraction_row)")}
    if have_rows and "qualifier" not in have_rows:
        db.execute("ALTER TABLE extraction_row ADD COLUMN qualifier TEXT")


_IMAGE_TYPES = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def image_type(data: bytes) -> str | None:
    return next((t for magic, t in _IMAGE_TYPES if data.startswith(magic)), None)


def _filename_date(name: str) -> str | None:
    """Fecha al inicio del nombre: 28-12-2022 o 2026-02-06."""
    if m := re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", name):
        y, mo, d = m.groups()
    elif m := re.match(r"\s*(\d{2})-(\d{2})-(\d{4})", name):
        d, mo, y = m.groups()
    else:
        return None
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _words(text: str) -> set[str]:
    plain = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    return {w[:5] for w in re.findall(r"[a-z]{4,}", plain)} - {"image", "imagen", "radio", "estud"}


def link_images(images: list[dict], studies: list[dict]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Asocia cada imagen a su informe: misma fecha y nombre parecido (o el único estudio de ese día)."""
    linked: dict[int, list[dict]] = {}
    loose: list[dict] = []
    for img in images:
        same_day = [s for s in studies if img["performed_on"] and s["performed_on"] == img["performed_on"]]
        words = _words(img["title"])
        scored = sorted(
            ((len(words & _words(f"{s['study_name'] or ''} {s['modality'] or ''}")), s) for s in same_day),
            key=lambda t: -t[0],
        )
        pick = None
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            pick = scored[0][1]
        elif len(same_day) == 1:
            pick = same_day[0]
        if pick:
            linked.setdefault(pick["id"], []).append(img)
        else:
            loose.append(img)
    return linked, loose


def ingest_image(
    db: sqlite3.Connection, vault: Vault, person: sqlite3.Row, filename: str, data: bytes
) -> int:
    """Imagen de un estudio (radiografía, ultrasonido...): se guarda cifrada y se ve en Estudios."""
    if len(data) > MAX_BYTES:
        raise IngestError(413, "El archivo pesa más de 25 MB.")
    media = image_type(data)
    if media is None:
        raise IngestError(415, "Solo se aceptan imágenes PNG o JPG y PDF.")
    sha = hashlib.sha256(data).hexdigest()
    if db.execute(
        "SELECT 1 FROM study_image WHERE person_id = ? AND file_sha256 = ?", (person["id"], sha)
    ).fetchone():
        raise IngestError(409, "Esta imagen ya estaba cargada.")
    title = re.sub(r"\.(png|jpe?g)$", "", filename, flags=re.I).strip() or "Imagen"
    cur = db.execute(
        "INSERT INTO study_image(person_id, title, performed_on, media_type, file_path, file_sha256) "
        "VALUES(?,?,?,?,?,?)",
        (person["id"], title, _filename_date(filename), media, vault.put(data), sha),
    )
    return cur.lastrowid


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
    kind: str = "laboratorio"  # o "imagen" (informe leído localmente, sin IA)


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
    if len(re.sub(r"\s", "", text)) < 40:  # escaneo: se lee con el OCR de macOS, en esta Mac
        try:
            text = ocr.ocr_bytes(data, ".pdf", vault.root.parent)
        except ocr.OcrUnavailable as e:
            raise IngestError(422, str(e)) from None
        if len(re.sub(r"\s", "", text)) < 40:
            raise IngestError(422, "No pude leer texto en este escaneo. ¿Está borroso o al revés?")

    # Laboratorio (columnas de resultado y referencia) o informe de un estudio (imagen, endoscopia,
    # patología, ECG...): el segundo se lee con reglas locales y se confirma junto al original.
    if not looks_like_lab(text) and (reports := parse_reports(text, filename)):
        return _ingest_imaging(db, vault, person, filename, data, sha, reports)

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


def _ingest_imaging(
    db: sqlite3.Connection,
    vault: Vault,
    person: sqlite3.Row,
    filename: str,
    data: bytes,
    sha: str,
    reports: list[ImagingReport],
) -> Ingested:
    """Informes de imagen: se leen aquí, en la Mac, con reglas; nada se envía a ninguna IA."""
    stored = vault.put(data)
    title = re.sub(r"\.pdf$", "", filename, flags=re.I).strip() or "Informe de imagen"
    first_date = next((r.performed_on for r in reports if r.performed_on), None)
    cur = db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256) "
        "VALUES(?, 'imagen', ?, ?, ?, ?)",
        (person["id"], title, first_date, stored, sha),
    )
    _store_imaging_drafts(db, cur.lastrowid, reports, first_date)
    return Ingested(cur.lastrowid, len(reports), "imagen")


def _store_imaging_drafts(
    db: sqlite3.Connection, doc_id: int, reports: list[ImagingReport], first_date: str | None
) -> None:
    db.execute(
        "INSERT INTO extraction(document_id, sent_text, redactions, provider, model, cost_usd, collected_on) "
        "VALUES(?, '', '{}', 'local', 'reglas', 0, ?)",
        (doc_id, first_date),
    )
    for pos, r in enumerate(reports):
        db.execute(
            "INSERT INTO imaging_draft(document_id, position, data) VALUES(?,?,?)",
            (doc_id, pos, json.dumps(r.to_dict(), ensure_ascii=False)),
        )


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
    if doc["doc_type"] == "imagen":
        original = vault.get(doc["file_path"])
        text = pdf_text(original)
        if len(re.sub(r"\s", "", text)) < 40:
            try:
                text = ocr.ocr_bytes(original, ".pdf", vault.root.parent)
            except ocr.OcrUnavailable as e:
                raise IngestError(422, str(e)) from None
        reports = parse_reports(text, doc["title"])
        if not reports:
            raise IngestError(422, "No se pudo volver a leer este informe de imagen.")
        first_date = next((r.performed_on for r in reports if r.performed_on), None)
        db.execute("DELETE FROM imaging_draft WHERE document_id = ?", (doc_id,))
        db.execute("DELETE FROM extraction WHERE document_id = ?", (doc_id,))
        db.execute("UPDATE document SET collected_on = ? WHERE id = ?", (first_date, doc_id))
        _store_imaging_drafts(db, doc_id, reports, first_date)
        return Ingested(doc_id, len(reports), "imagen")
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
        "problems, converted, qualifier) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
            r.qualifier,
        ),
    )


def review_payload(db: sqlite3.Connection, doc_id: int) -> dict:
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    ex = db.execute("SELECT * FROM extraction WHERE document_id = ?", (doc_id,)).fetchone()
    rows = []
    for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ? ORDER BY id", (doc_id,)):
        a = terminology.BY_KEY.get(r["analyte_key"] or "")
        problems = json.loads(r["problems"])
        if critical.check(r["analyte_key"], r["value_num"], r["qualifier"]):
            problems.append("valor_critico")
        rows.append(
            {
                **dict(r),
                "name": a.name if a else None,
                "problems": problems,
                "needs_attention": bool(problems) or bool(r["converted"]),
            }
        )
    imaging = [
        {"position": d["position"], **json.loads(d["data"])}
        for d in db.execute(
            "SELECT position, data FROM imaging_draft WHERE document_id = ? ORDER BY position", (doc_id,)
        )
    ]
    return {
        "document": {
            k: doc[k]
            for k in ("id", "person_id", "doc_type", "title", "collected_on", "review_state", "uploaded_at")
        },
        "ai_saw": ex["sent_text"] if ex else None,
        "redactions": json.loads(ex["redactions"]) if ex else {},
        "rows": rows,
        "imaging": imaging,
    }


def _typed_value(analyte: terminology.Analyte, text: str) -> tuple[float | None, str | None, str | None]:
    """Interpreta lo que la persona escribió según el tipo del análisis: (número, texto, signo < o >)."""
    t = (text or "").strip()
    if not t:
        raise IngestError(422, f"Falta el valor de «{analyte.name}».")
    qualifier = None
    if m := re.match(r"^(<=?|>=?)\s*(\d+(?:[.,]\d+)?)$", t):
        qualifier, t = m.group(1), m.group(2)
    try:
        num: float | None = float(t.replace(",", "."))
    except ValueError:
        num = None
    if analyte.kind == "num":
        if num is None:
            raise IngestError(422, f"«{analyte.name}» es un número: escribe solo la cifra.")
        return num, None, qualifier
    return (num, None, qualifier) if num is not None else (None, t, None)


def _resolved(
    analyte: terminology.Analyte, text: str, unit_text: str | None, ref_text: str | None
) -> tuple[float | None, str | None, str, ranges.Ref, str | None, str | None]:
    """Valor en unidad canónica, su rango convertido, su estado y su signo (< o >), desde lo impreso."""
    num, txt, qualifier = _typed_value(analyte, text)
    if num is None:
        return None, txt, analyte.unit, ranges.Ref(), ranges.classify_text(txt or "", ref_text), None
    unit_text = unit_text or analyte.unit
    if analyte.kind == "qual":  # un conteo en un análisis de texto: sin conversión
        value, unit = num, ""
    else:
        try:
            value, unit = units.to_canonical(analyte.key, num, unit_text, analyte.unit)
        except units.UnknownUnit:
            raise IngestError(
                422, f"No sé convertir «{unit_text}» a {analyte.unit} para {analyte.name}."
            ) from None
    ref = convert_ref(analyte.key, ranges.parse_ref_full(ref_text), unit_text, analyte.unit)
    status = ranges.classify_censored(qualifier, value, ref) if qualifier else ranges.classify_ref(value, ref)
    return value, None, unit, ref, status, qualifier


def _insert_observation(db: sqlite3.Connection, doc: sqlite3.Row, **f) -> None:
    db.execute(
        "INSERT INTO observation(person_id, document_id, analyte_key, loinc, printed_name, value_num, "
        "value_text, unit, value_printed, unit_printed, ref_low, ref_high, ref_printed, method, status, "
        "collected_on, confirmed_by, confirmed_at, entered_manually, qualifier) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            doc["person_id"],
            doc["id"],
            f["key"],
            f["analyte"].loinc or None,
            f["printed_name"],
            f["value_num"],
            f["value_text"],
            f["unit"],
            f["value_printed"],
            f["unit_printed"],
            f["ref"].low,
            f["ref"].high,
            f["ref_printed"],
            f["method"],
            f["status"],
            f["collected_on"],
            f["reviewer_id"],
            f["now"],
            f["manual"],
            f.get("qualifier"),
        ),
    )


def confirm_review(
    db: sqlite3.Connection,
    doc_id: int,
    reviewer_id: int,
    collected_on: str,
    decisions: list[dict],
    manual: list[dict] | None = None,
) -> int:
    """Guarda en `observation` solo lo aceptado: filas del PDF (con correcciones o con el análisis que la
    persona indicó) y resultados que faltaban, escritos a mano y marcados como tales."""
    date.fromisoformat(collected_on)
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if doc["review_state"] != "pendiente":
        raise IngestError(409, "Este estudio ya fue revisado.")
    rows = {r["id"]: r for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ?", (doc_id,))}
    now = datetime.now().isoformat(timespec="seconds")
    common = {"collected_on": collected_on, "reviewer_id": reviewer_id, "now": now}
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
        if key != r["analyte_key"]:
            # La persona indicó (o cambió) el análisis: el valor se interpreta de nuevo desde lo impreso.
            text = d.get("printed_value") or r["value_printed"]
            value_num, value_text, unit, ref, status, qualifier = _resolved(
                analyte, text, r["unit_printed"], r["ref_printed"]
            )
        else:
            value_num = d.get("value_num", r["value_num"])
            value_text = d.get("value_text", r["value_text"])
            if value_num is None and not value_text:
                raise IngestError(422, f"«{r['printed_name']}» no tiene valor.")
            qualifier = r["qualifier"]
            ref = ranges.parse_ref_full(r["ref_printed"])
            if value_num is not None:
                ref = convert_ref(key, ref, r["unit_printed"], analyte.unit)  # mismo criterio que el valor
                status = (
                    ranges.classify_censored(qualifier, value_num, ref)
                    if qualifier
                    else ranges.classify_ref(value_num, ref)
                )
            else:
                status = ranges.classify_text(value_text, r["ref_printed"])
            unit = d.get("unit", r["unit"]) or analyte.unit
        _insert_observation(
            db,
            doc,
            key=key,
            analyte=analyte,
            printed_name=r["printed_name"],
            value_num=value_num,
            value_text=value_text,
            unit=unit,
            value_printed=r["value_printed"],
            unit_printed=r["unit_printed"],
            ref=ref,
            ref_printed=r["ref_printed"],
            method=r["method"],
            status=status,
            qualifier=qualifier,
            manual=0,
            **common,
        )
        saved += 1
    for m in manual or []:
        analyte = terminology.BY_KEY.get(m.get("analyte_key") or "")
        if analyte is None:
            raise IngestError(422, "Elige el análisis que quieres agregar.")
        value_num, value_text, unit, ref, status, qualifier = _resolved(
            analyte, m.get("value") or "", m.get("unit"), m.get("ref")
        )
        _insert_observation(
            db,
            doc,
            key=analyte.key,
            analyte=analyte,
            printed_name=analyte.name,
            value_num=value_num,
            value_text=value_text,
            unit=unit,
            value_printed=(m.get("value") or "").strip(),
            unit_printed=(m.get("unit") or None),
            ref=ref,
            ref_printed=(m.get("ref") or None),
            method=None,
            status=status,
            qualifier=qualifier,
            manual=1,
            **common,
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
        "SELECT id, analyte_key, value_num, unit, unit_printed, ref_printed, ref_low, ref_high, status, "
        "qualifier FROM observation WHERE value_num IS NOT NULL"  # también los que no traían rango
    ).fetchall()
    for r in rows:
        analyte = terminology.BY_KEY.get(r["analyte_key"])
        if analyte is None:
            continue
        ref = convert_ref(
            r["analyte_key"], ranges.parse_ref_full(r["ref_printed"]), r["unit_printed"], analyte.unit
        )
        status = (
            ranges.classify_censored(r["qualifier"], r["value_num"], ref)
            if r["qualifier"]
            else ranges.classify_ref(r["value_num"], ref)
        )
        if (ref.low, ref.high, status) != (r["ref_low"], r["ref_high"], r["status"]):
            db.execute(
                "UPDATE observation SET ref_low = ?, ref_high = ?, status = ? WHERE id = ?",
                (ref.low, ref.high, status, r["id"]),
            )
            changed += 1
    return changed


def confirm_imaging_review(
    db: sqlite3.Connection, doc_id: int, reviewer_id: int, decisions: list[dict]
) -> int:
    """Guarda en `imaging_study` solo los informes aceptados (con las correcciones de la persona)."""
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if doc["doc_type"] != "imagen":
        raise IngestError(422, "Este estudio no es un informe de imagen.")
    if doc["review_state"] != "pendiente":
        raise IngestError(409, "Este estudio ya fue revisado.")
    drafts = {
        d["position"]: json.loads(d["data"])
        for d in db.execute("SELECT position, data FROM imaging_draft WHERE document_id = ?", (doc_id,))
    }
    now = datetime.now().isoformat(timespec="seconds")
    saved, first_date = 0, None
    for dec in decisions:
        draft = drafts.get(dec.get("position"))
        if draft is None:
            raise IngestError(422, "Informe desconocido.")
        if not dec.get("accept"):
            continue
        performed_on = dec.get("performed_on") or draft.get("performed_on")
        try:
            date.fromisoformat(performed_on or "")
        except ValueError:
            raise IngestError(422, "Indica la fecha del estudio (AAAA-MM-DD).") from None
        study = (dec.get("study_name") or draft["study_name"]).strip()
        flag = dec.get("flag") or draft.get("flag") or "revisar"
        if flag not in ("normal", "revisar"):
            raise IngestError(422, "Marca inválida.")
        report_text = " ".join(
            draft.get(k, "") for k in ("technique", "indication", "findings", "conclusion", "suggestions")
        ).strip()
        db.execute(
            "INSERT INTO imaging_study(person_id, document_id, modality, region, performed_on, report_text, "
            "study_name, technique, indication, findings, prior, conclusion, suggestions, radiologist, site, "
            "flag, confirmed_by, confirmed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc["person_id"],
                doc_id,
                (dec.get("modality") or draft.get("modality") or "Otro"),
                study,
                performed_on,
                report_text,
                study,
                draft.get("technique"),
                draft.get("indication"),
                draft.get("findings"),
                draft.get("prior"),
                draft.get("conclusion"),
                draft.get("suggestions"),
                draft.get("radiologist"),
                draft.get("site"),
                flag,
                reviewer_id,
                now,
            ),
        )
        first_date = first_date or performed_on
        saved += 1
    db.execute("DELETE FROM imaging_draft WHERE document_id = ?", (doc_id,))
    db.execute(
        "UPDATE document SET review_state = 'revisada', "
        "collected_on = COALESCE(?, collected_on) WHERE id = ?",
        (first_date, doc_id),
    )
    return saved
