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
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

from ..extract import Outcome, Row, convert_ref, extract_document
from ..imaging import ImagingReport, looks_like_lab, parse_reports
from ..normalize import critical, labs, ranges, terminology, units
from ..privacy import Anonymizer
from ..providers import BudgetExceeded, ProviderError, Router
from . import bodyscan, clinical, health, ocr, prescriptions, tables
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

CUSTOM_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_analyte (
  key        TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  unit       TEXT NOT NULL DEFAULT '',
  kind       TEXT NOT NULL CHECK (kind IN ('num', 'qual')),
  alias      TEXT,                                  -- nombre impreso con el que se creó
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

PRESCRIPTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS prescription_draft (
  document_id INTEGER PRIMARY KEY REFERENCES document(id) ON DELETE CASCADE,
  data        TEXT NOT NULL                        -- receta leída, pendiente de revisar (JSON)
);
"""

LINK_SCHEMA = """
CREATE TABLE IF NOT EXISTS study_link (
  a INTEGER NOT NULL REFERENCES imaging_study(id) ON DELETE CASCADE,
  b INTEGER NOT NULL REFERENCES imaging_study(id) ON DELETE CASCADE,
  PRIMARY KEY (a, b), CHECK (a < b)
);
"""

# Procedimientos que se hacen juntos: el mismo día, la conclusión, las imágenes y las biopsias van de la mano.
_TOGETHER = {"Endoscopia", "Patología"}


def related_studies(studies: list[dict], manual: set[tuple[int, int]]) -> dict[int, list[dict]]:
    """Estudios relacionados: los vinculados a mano y las endoscopias y biopsias del mismo día."""
    out: dict[int, list[dict]] = {s["id"]: [] for s in studies}
    by_id = {s["id"]: s for s in studies}
    for a in studies:
        for b in studies:
            if a["id"] >= b["id"]:
                continue
            same_day = a["performed_on"] == b["performed_on"]
            auto = same_day and (
                (a["modality"] in _TOGETHER and b["modality"] in _TOGETHER)
                or (
                    a["modality"] and a["modality"] == b["modality"]
                )  # p. ej. el resumen y el trazo de un ECG
            )
            if auto or (a["id"], b["id"]) in manual:
                manual_link = (a["id"], b["id"]) in manual
                out[a["id"]].append({**_brief_study(by_id[b["id"]]), "manual": manual_link})
                out[b["id"]].append({**_brief_study(by_id[a["id"]]), "manual": manual_link})
    return out


def _brief_study(s: dict) -> dict:
    return {k: s[k] for k in ("id", "study_name", "modality", "performed_on", "document_id")}


# Columnas que se agregaron a imaging_study después de la primera versión del esquema.
_IMAGING_COLUMNS = {
    "study_name": "TEXT", "technique": "TEXT", "indication": "TEXT", "findings": "TEXT", "prior": "TEXT",
    "conclusion": "TEXT", "suggestions": "TEXT", "radiologist": "TEXT", "site": "TEXT", "flag": "TEXT",
    "confirmed_by": "INTEGER", "confirmed_at": "TEXT", "tables_json": "TEXT", "name_source": "TEXT",
}  # fmt: skip


def migrate_imaging(db: sqlite3.Connection) -> None:
    """Prepara bases creadas antes de los informes de imagen, sin tocar sus datos."""
    db.executescript(IMAGING_SCHEMA)
    db.executescript(IMAGE_SCHEMA)
    db.executescript(LINK_SCHEMA)
    db.executescript(PRESCRIPTION_SCHEMA)
    db.executescript(bodyscan.SCHEMA)
    db.executescript(CUSTOM_SCHEMA)
    have = {r["name"] for r in db.execute("PRAGMA table_info(imaging_study)")}
    for col, typ in _IMAGING_COLUMNS.items():
        if col not in have:
            db.execute(f"ALTER TABLE imaging_study ADD COLUMN {col} {typ}")


def migrate_document(db: sqlite3.Connection) -> None:
    """Agrega a `document` el nombre original del archivo (el título es el nombre visible y corregible)."""
    have = {r["name"] for r in db.execute("PRAGMA table_info(document)")}
    if have and "filename" not in have:
        db.execute("ALTER TABLE document ADD COLUMN filename TEXT")
    if have and "name_source" not in have:
        db.execute("ALTER TABLE document ADD COLUMN name_source TEXT")  # ia | manual


def rename_document(db: sqlite3.Connection, doc_id: int, title: str) -> None:
    title = " ".join((title or "").split())
    if not 2 <= len(title) <= 120:
        raise IngestError(422, "El nombre debe tener entre 2 y 120 caracteres.")
    # El nombre del archivo original se conserva la primera vez que se cambia el título.
    db.execute(
        "UPDATE document SET filename = COALESCE(filename, title), title = ?, name_source = 'manual' "
        "WHERE id = ?",
        (title, doc_id),
    )


def rename_study(db: sqlite3.Connection, study_id: int, name: str) -> None:
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 120:
        raise IngestError(422, "El nombre debe tener entre 2 y 120 caracteres.")
    db.execute(
        "UPDATE imaging_study SET study_name = ?, name_source = 'manual' WHERE id = ?", (name, study_id)
    )


def migrate_observation(db: sqlite3.Connection) -> None:
    """Agrega a `observation` la marca de resultado escrito a mano y el signo < / > (bases anteriores)."""
    have = {r["name"] for r in db.execute("PRAGMA table_info(observation)")}
    if have and "entered_manually" not in have:
        db.execute("ALTER TABLE observation ADD COLUMN entered_manually INTEGER NOT NULL DEFAULT 0")
    if have and "qualifier" not in have:
        db.execute("ALTER TABLE observation ADD COLUMN qualifier TEXT")
    have_rows = {r["name"] for r in db.execute("PRAGMA table_info(extraction_row)")}
    if have_rows and "ignored" not in have_rows:
        db.execute("ALTER TABLE extraction_row ADD COLUMN ignored INTEGER NOT NULL DEFAULT 0")
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


def load_custom(db: sqlite3.Connection) -> None:
    """Carga al catálogo en uso los análisis propios guardados (y quita los de otra base en este proceso)."""
    terminology.clear_custom()
    for r in db.execute("SELECT * FROM custom_analyte ORDER BY created_at, key"):
        terminology.register_custom(_custom_analyte(r["key"], r["name"], r["unit"], r["kind"], r["alias"]))


def _custom_analyte(key: str, name: str, unit: str, kind: str, alias: str | None) -> terminology.Analyte:
    return terminology.Analyte(key, name, "", unit, "otros", (alias,) if alias else (), kind)


def create_custom(
    db: sqlite3.Connection, name: str, unit: str, kind: str, alias: str | None = None
) -> terminology.Analyte:
    """Crea un análisis propio para lo que el catálogo no trae. Aparece en el menú y tiene su gráfica."""
    name, unit, alias = name.strip(), (unit or "").strip(), (alias or "").strip() or None
    if not 2 <= len(name) <= 80:
        raise IngestError(422, "Escribe un nombre de entre 2 y 80 letras.")
    if len(unit) > 20:
        raise IngestError(422, "La unidad es muy larga (máximo 20 caracteres).")
    if kind not in ("num", "qual"):
        raise IngestError(422, "El resultado es un número (num) o un texto (qual).")
    if terminology.is_standard_name(name):
        raise IngestError(409, f"«{name}» ya está en el catálogo: elígelo del menú.")
    base = terminology.CUSTOM_PREFIX + terminology.slug(name)
    key, n = base, 1
    while key in terminology.BY_KEY:
        n += 1
        key = f"{base}_{n}"
    db.execute(
        "INSERT INTO custom_analyte(key, name, unit, kind, alias) VALUES(?,?,?,?,?)",
        (key, name, "" if kind == "qual" else unit, kind, alias),
    )
    analyte = _custom_analyte(key, name, "" if kind == "qual" else unit, kind, alias)
    terminology.register_custom(analyte)
    return analyte


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
    used_fallback: bool = False  # el lector básico no entendió el formato y lo leyó Claude


def _poor(o: Outcome) -> bool:
    """El lector básico no entendió el formato: nada leído, muchos análisis sin reconocer o sin fecha."""
    unknown = sum(1 for r in o.rows if not r.key)
    return not o.rows or o.collected_on is None or unknown / len(o.rows) > 0.25


def read_lab_text(
    text: str,
    router: Router,
    fallback: Router | None,
    person_names_: list[str],
    today: date | None,
) -> tuple[Outcome, bool]:
    """Lee con el lector principal (el básico, local) y, solo si no entendió el formato, con Claude.

    Si Claude falla o no lee más análisis reconocidos, se conserva lo del lector principal.
    """
    kwargs = {"anonymizer": Anonymizer(person_names_), "reference_date": today or date.today()}
    outcome = extract_document(text, router, **kwargs)
    if fallback is None or not _poor(outcome):
        return outcome, False
    try:
        better = extract_document(text, fallback, **kwargs)
    except (ProviderError, BudgetExceeded):
        return outcome, False
    known = lambda o: sum(1 for r in o.rows if r.key)  # noqa: E731
    return (better, True) if known(better) > known(outcome) else (outcome, False)


def ingest_pdf(
    db: sqlite3.Connection,
    vault: Vault,
    router: Router,
    person: sqlite3.Row,
    filename: str,
    data: bytes,
    today: date | None = None,
    fallback: Router | None = None,
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

    outcome, used_fallback = read_lab_text(text, router, fallback, person_names(db, person), today)
    stored = vault.put(data)
    title = re.sub(r"\.pdf$", "", filename, flags=re.I).strip() or "Estudio"
    cur = db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256, filename, "
        "source_name) VALUES(?, 'laboratorio', ?, ?, ?, ?, ?, ?)",
        (person["id"], title, outcome.collected_on, stored, sha, filename, labs.detect_lab(text)),
    )
    _store_extraction(db, cur.lastrowid, person["id"], outcome)
    return Ingested(cur.lastrowid, len(outcome.rows), used_fallback=used_fallback)


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
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256, filename) "
        "VALUES(?, 'imagen', ?, ?, ?, ?, ?)",
        (person["id"], title, first_date, stored, sha, filename),
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


_TEXT_FIELDS = (
    "technique",
    "indication",
    "findings",
    "prior",
    "conclusion",
    "suggestions",
    "radiologist",
    "site",
)


def read_study_tables(db: sqlite3.Connection, vault: Vault, router: Router, study_id: int) -> dict:
    """Transcribe con Claude las tablas de un informe (páginas como imagen) y las guarda con el informe."""
    study = db.execute(
        "SELECT i.id, d.file_path FROM imaging_study i JOIN document d ON d.id = i.document_id "
        "WHERE i.id = ?",
        (study_id,),
    ).fetchone()
    if study is None:
        raise IngestError(404, "No encontré ese informe.")
    original = vault.get(study["file_path"])
    import pdfplumber

    with pdfplumber.open(io.BytesIO(original)) as pdf:
        pages = min(len(pdf.pages), tables.MAX_PAGES)
    images = [render_page(original, n, resolution=150) for n in range(1, pages + 1)]
    result = tables.read_tables(router, images)
    if not result["tables"]:
        raise IngestError(422, "No pude leer tablas en este informe.")
    db.execute(
        "UPDATE imaging_study SET tables_json = ? WHERE id = ?",
        (json.dumps(result, ensure_ascii=False), study_id),
    )
    return result


def refresh_imaging_text(db: sqlite3.Connection, vault: Vault, person_id: int) -> dict:
    """Vuelve a leer, con la versión actual del lector, los informes ya confirmados de una persona.

    Solo se actualizan los textos (técnica, hallazgos, conclusión…). Lo que la persona confirmó (nombre,
    fecha, tipo y marca) no se toca, y si el número de informes del PDF cambió, ese documento se salta.
    """
    counts = {"updated": 0, "unchanged": 0, "skipped": 0}
    docs = db.execute(
        "SELECT id, title, file_path FROM document WHERE person_id = ? AND doc_type = 'imagen' "
        "AND review_state = 'revisada' ORDER BY id",
        (person_id,),
    ).fetchall()
    for d in docs:
        existing = db.execute(
            "SELECT id, " + ", ".join(_TEXT_FIELDS) + " FROM imaging_study WHERE document_id = ? ORDER BY id",
            (d["id"],),
        ).fetchall()
        try:
            original = vault.get(d["file_path"])
            text = pdf_text(original)
            if len(re.sub(r"\s", "", text)) < 40:
                text = ocr.ocr_bytes(original, ".pdf", vault.root.parent)
            reports = parse_reports(text, d["title"])
        except Exception:  # noqa: BLE001 - un documento que no se puede leer no detiene a los demás
            counts["skipped"] += 1
            continue
        if len(reports) != len(existing) or not existing:
            counts["skipped"] += 1
            continue
        changed = False
        for row, rep in zip(existing, reports, strict=True):
            new = {f: getattr(rep, f, None) or "" for f in _TEXT_FIELDS}
            if any((row[f] or "") != new[f] for f in _TEXT_FIELDS):
                db.execute(
                    "UPDATE imaging_study SET "
                    + ", ".join(f"{f} = ?" for f in _TEXT_FIELDS)
                    + " WHERE id = ?",
                    (*(new[f] for f in _TEXT_FIELDS), row["id"]),
                )
                changed = True
        counts["updated" if changed else "unchanged"] += 1
    return counts


def reread(
    db: sqlite3.Connection,
    vault: Vault,
    router: Router,
    doc_id: int,
    person: sqlite3.Row,
    today: date | None = None,
    fallback: Router | None = None,
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
    text = pdf_text(vault.get(doc["file_path"]))
    outcome, used_fallback = read_lab_text(text, router, fallback, person_names(db, person), today)
    db.execute("DELETE FROM extraction_row WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM extraction WHERE document_id = ?", (doc_id,))
    db.execute(
        "UPDATE document SET collected_on = ?, source_name = COALESCE(?, source_name) WHERE id = ?",
        (outcome.collected_on, labs.detect_lab(text), doc_id),
    )
    _store_extraction(db, doc_id, person["id"], outcome)
    return Ingested(doc_id, len(outcome.rows), used_fallback=used_fallback)


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


_NOT_SAVED_REASON = {
    "analito_desconocido": ("No reconocí este análisis", True),
    "no_respaldada_por_el_documento": ("No aparece tal cual en el documento", True),
    "unidad_no_reconocida": ("No reconocí la unidad", True),
    "valor_no_numerico": ("El valor no es un número", True),
    "mismo_valor_en_otra_unidad": ("Es el mismo resultado en otra unidad, ya está guardado", False),
    "aparece_mas_de_una_vez": ("Aparece más de una vez en el estudio", False),
}


def imaging_gaps(db: sqlite3.Connection, doc_id: int, reviewed: bool) -> list[dict]:
    """Informes de imagen a los que House no les encontró la fecha o una conclusión separada."""
    if reviewed:
        found = [
            dict(r)
            for r in db.execute(
                "SELECT study_name, performed_on, conclusion FROM imaging_study WHERE document_id = ?",
                (doc_id,),
            )
        ]
    else:
        found = [
            json.loads(r["data"])
            for r in db.execute(
                "SELECT data FROM imaging_draft WHERE document_id = ? ORDER BY position", (doc_id,)
            )
        ]
    gaps = []
    for f in found:
        missing = []
        if not f.get("performed_on"):
            missing.append("la fecha")
        if not (f.get("conclusion") or "").strip():
            missing.append("una conclusión separada (lee el informe completo)")
        if missing:
            gaps.append({"study_name": f.get("study_name") or "Informe", "missing": missing})
    return gaps


def _saved_counter(db: sqlite3.Connection, doc_id: int) -> Counter:
    """Cuántas veces está guardado cada (nombre, valor) impreso de un estudio, sin lo escrito a mano."""
    return Counter(
        (o["printed_name"], o["value_printed"])
        for o in db.execute(
            "SELECT printed_name, value_printed FROM observation "
            "WHERE document_id = ? AND entered_manually = 0",
            (doc_id,),
        )
    )


def refresh_missing(
    db: sqlite3.Connection,
    vault: Vault,
    router: Router,
    doc_id: int,
    person: sqlite3.Row,
    today: date | None = None,
    fallback: Router | None = None,
) -> int:
    """Estudio ya revisado: vuelve a leer el original con el lector actual y deja abiertos solo los
    renglones que no están guardados. Lo ya guardado no se toca. Devuelve cuántos quedan por resolver."""
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if doc["doc_type"] != "laboratorio" or doc["review_state"] != "revisada":
        raise IngestError(409, "Solo se puede completar un estudio de laboratorio ya revisado.")
    outcome, _ = read_lab_text(
        pdf_text(vault.get(doc["file_path"])), router, fallback, person_names(db, person), today
    )
    saved = _saved_counter(db, doc_id)
    keep = Counter(saved)
    ignored = set()
    for r in db.execute(
        "SELECT id, printed_name, value_printed, ignored FROM extraction_row WHERE document_id = ?", (doc_id,)
    ):
        k = (r["printed_name"], r["value_printed"])
        if keep[k] > 0:
            keep[k] -= 1  # este renglón es un resultado guardado
        else:
            if r["ignored"]:
                ignored.add(k)
            db.execute("DELETE FROM extraction_row WHERE id = ?", (r["id"],))
    for r in outcome.rows:
        k = (r.printed_name, r.value_text)
        if saved[k] > 0:
            saved[k] -= 1
            continue
        _save_row(db, doc_id, r)
        if k in ignored:
            db.execute(
                "UPDATE extraction_row SET ignored = 1 WHERE document_id = ? AND printed_name = ? "
                "AND value_printed = ?",
                (doc_id, r.printed_name, r.value_text),
            )
    return len(not_saved_rows(db, doc_id, True))


def not_saved_rows(db: sqlite3.Connection, doc_id: int, reviewed: bool) -> list[dict]:
    """Renglones que House leyó del PDF pero que no están (o no estarán) entre los resultados guardados.

    `lost` = True cuando de verdad falta un resultado (no se reconoció o la persona lo descartó);
    False cuando es un repetido que ya está guardado.
    """
    saved = _saved_counter(db, doc_id) if reviewed else Counter()
    out = []
    for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ? ORDER BY id", (doc_id,)):
        if r["ignored"]:
            continue  # la persona indicó que no es un resultado (control, leyenda...)
        problems = json.loads(r["problems"])
        reason, lost = None, True
        for p in problems:
            if p in _NOT_SAVED_REASON:
                reason, lost = _NOT_SAVED_REASON[p]
                break
        key = (r["printed_name"], r["value_printed"])
        if reviewed:
            if saved.get(key, 0) > 0:
                saved[key] -= 1
                continue
            reason = reason or "Decidiste no guardarlo"
        elif r["analyte_key"] and reason is None:
            continue  # sin problemas: se guardará al confirmar
        out.append(
            {
                "id": r["id"],
                "printed_name": r["printed_name"],
                "value_printed": r["value_printed"],
                "unit_printed": r["unit_printed"],
                "reason": reason or "No reconocí este análisis",
                "lost": lost,
            }
        )
    return out


def prescription_images(data: bytes) -> tuple[list[bytes], str]:
    """Páginas de una receta como imágenes para Claude: de un PDF, o una foto reducida y enderezada."""
    if data.startswith(b"%PDF"):
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = min(len(pdf.pages), 4)
        return [render_page(data, n, resolution=150) for n in range(1, pages + 1)], "pdf"
    if image_type(data) is None:
        raise IngestError(415, "Sube una foto (PNG o JPG) o un PDF de la receta.")
    from PIL import Image, ImageOps

    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception:
        raise IngestError(422, "No pude abrir la imagen.") from None
    img.thumbnail((2000, 2000))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return [out.getvalue()], "image"


def ingest_prescription(
    db: sqlite3.Connection, vault: Vault, router: Router, person: sqlite3.Row, filename: str, data: bytes
) -> Ingested:
    """Receta (foto o PDF): Claude propone los medicamentos y la persona los revisa junto al original."""
    if len(data) > MAX_BYTES:
        raise IngestError(413, "El archivo pesa más de 25 MB.")
    sha = hashlib.sha256(data).hexdigest()
    dup = db.execute(
        "SELECT id FROM document WHERE person_id = ? AND file_sha256 = ?", (person["id"], sha)
    ).fetchone()
    if dup:
        raise IngestError(409, "Esta receta ya estaba cargada.", dup["id"])
    images, kind = prescription_images(data)
    result = prescriptions.read_prescription(router, images)
    if not result["medications"]:
        raise IngestError(422, "No encontré medicamentos en esta receta. ¿Se ve completa y derecha?")
    result["file_kind"] = kind
    title = re.sub(r"\.(pdf|png|jpe?g)$", "", filename, flags=re.I).strip() or "Receta"
    cur = db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256, filename) "
        "VALUES(?, 'receta', ?, ?, ?, ?, ?)",
        (person["id"], title, result["prescription_date"], vault.put(data), sha, filename),
    )
    db.execute(
        "INSERT INTO prescription_draft(document_id, data) VALUES(?, ?)",
        (cur.lastrowid, json.dumps(result, ensure_ascii=False)),
    )
    return Ingested(cur.lastrowid, len(result["medications"]), "receta")


def ingest_body_scan(
    db: sqlite3.Connection, vault: Vault, router: Router, person: sqlite3.Row, filename: str, data: bytes
) -> Ingested:
    """Reporte de composición corporal (InBody): Claude transcribe y la persona confirma antes de guardar."""
    if len(data) > MAX_BYTES:
        raise IngestError(413, "El archivo pesa más de 25 MB.")
    sha = hashlib.sha256(data).hexdigest()
    dup = db.execute(
        "SELECT id FROM document WHERE person_id = ? AND file_sha256 = ?", (person["id"], sha)
    ).fetchone()
    if dup:
        raise IngestError(409, "Este reporte ya estaba cargado.", dup["id"])
    images, kind = prescription_images(data)
    result = bodyscan.read_scan(router, images)
    if not result["metrics"]:
        raise IngestError(
            422, "No encontré datos de composición corporal. ¿Se ve completo y derecho el reporte?"
        )
    result["file_kind"] = kind
    cur = db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256, filename, "
        "name_source) VALUES(?, 'otro', ?, ?, ?, ?, ?, 'ia')",
        (person["id"], "Composición corporal", result["measured_on"], vault.put(data), sha, filename),
    )
    db.execute(
        "INSERT INTO body_scan(document_id, person_id, measured_on, confirmed, data) VALUES(?,?,?,0,?)",
        (cur.lastrowid, person["id"], result["measured_on"], json.dumps(result, ensure_ascii=False)),
    )
    return Ingested(cur.lastrowid, len(result["metrics"]), "composicion")


def confirm_body_scan(db: sqlite3.Connection, doc_id: int, person_id: int, body: dict) -> int:
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    scan = db.execute("SELECT confirmed FROM body_scan WHERE document_id = ?", (doc_id,)).fetchone()
    if scan is None:
        raise IngestError(422, "Este documento no es un reporte de composición corporal.")
    if scan["confirmed"] or doc["review_state"] != "pendiente":
        raise IngestError(409, "Este reporte ya fue revisado.")
    when = (body.get("measured_on") or "").strip()
    try:
        date.fromisoformat(when)
    except ValueError:
        raise IngestError(422, "Indica la fecha del reporte (AAAA-MM-DD).") from None
    keys = [k for k in body.get("keys", []) if k in bodyscan.METRICS]
    if not keys:
        raise IngestError(422, "Elige al menos una medida para guardar.")
    height = body.get("height_cm") if body.get("update_height") else None
    try:
        saved = bodyscan.save_confirmed(db, person_id, doc_id, when, keys, float(height) if height else None)
    except health.HealthError as e:
        raise IngestError(e.status, e.message) from None
    db.execute("UPDATE document SET review_state = 'revisada', collected_on = ? WHERE id = ?", (when, doc_id))
    return saved


def confirm_prescription(db: sqlite3.Connection, doc_id: int, person_id: int, body: dict) -> int:
    """Guarda como medicamentos solo lo aceptado (corregido por la persona) y lo liga a un padecimiento."""
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    draft = db.execute("SELECT data FROM prescription_draft WHERE document_id = ?", (doc_id,)).fetchone()
    if doc["doc_type"] != "receta":
        raise IngestError(422, "Este documento no es una receta.")
    if doc["review_state"] != "pendiente" or draft is None:
        raise IngestError(409, "Esta receta ya fue revisada.")
    when = (body.get("prescription_date") or "").strip() or None
    if when:
        try:
            date.fromisoformat(when)
        except ValueError:
            raise IngestError(422, "Fecha inválida (usa AAAA-MM-DD).") from None
    picked = [d for d in body.get("decisions", []) if d.get("accept")]
    if not picked:
        raise IngestError(422, "Elige al menos un medicamento para guardar.")
    problem_id = body.get("problem_id")
    saved = 0
    for d in picked:
        name = (d.get("name") or d.get("active_ingredient") or d.get("brand") or "").strip()
        if not name:
            raise IngestError(422, "Todos los medicamentos que guardes necesitan nombre.")
        dose = " ".join(x.strip() for x in (d.get("dose") or "", d.get("frequency") or "") if x and x.strip())
        notes = "; ".join(
            x.strip() for x in (d.get("duration") or "", d.get("instructions") or "") if x and x.strip()
        )
        fields = {
            "name": name,
            "active_ingredient": (d.get("active_ingredient") or "").strip() or None,
            "brand": (d.get("brand") or "").strip() or None,
            "dose": dose or None,
            "reason": (body.get("diagnosis") or "").strip() or None,
            "prescriber": (body.get("prescriber") or "").strip() or None,
            "since_year": when[:4] if when else None,
            "active": bool(d.get("active", True)),
            "notes": notes or None,
        }
        try:
            # Al borrar una receta sus medicamentos se conservan; si la misma receta se vuelve a subir,
            # se retoma ese medicamento idéntico en vez de guardarlo otra vez.
            same = db.execute(
                "SELECT id FROM medication WHERE person_id = ? AND document_id IS NULL AND name = ? "
                "AND active_ingredient IS ? AND brand IS ? AND dose IS ? AND reason IS ? "
                "AND prescriber IS ? AND since_year IS ? AND notes IS ? ORDER BY id LIMIT 1",
                (
                    person_id,
                    *(
                        fields[k]
                        for k in (
                            "name",
                            "active_ingredient",
                            "brand",
                            "dose",
                            "reason",
                            "prescriber",
                            "since_year",
                            "notes",
                        )
                    ),
                ),
            ).fetchone()
            if same:
                mid = same["id"]
                db.execute("UPDATE medication SET active = ? WHERE id = ?", (int(fields["active"]), mid))
            else:
                mid = clinical.add(db, person_id, "medication", fields)
            db.execute("UPDATE medication SET document_id = ? WHERE id = ?", (doc_id, mid))
            if problem_id:
                clinical.add_link(db, person_id, int(problem_id), "medication", str(mid))
        except clinical.ClinicalError as e:
            raise IngestError(e.status, e.message) from None
        saved += 1
    db.execute("UPDATE document SET review_state = 'revisada', collected_on = ? WHERE id = ?", (when, doc_id))
    db.execute("DELETE FROM prescription_draft WHERE document_id = ?", (doc_id,))
    return saved


def review_payload(db: sqlite3.Connection, doc_id: int) -> dict:
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    scan = db.execute("SELECT data, confirmed FROM body_scan WHERE document_id = ?", (doc_id,)).fetchone()
    if scan is not None:
        prof = health.overview(db, doc["person_id"])["profile"]
        return {
            "document": {
                k: doc[k] for k in ("id", "person_id", "doc_type", "title", "collected_on", "review_state")
            },
            "ai_saw": "",
            "redactions": {},
            "rows": [],
            "imaging": [],
            "body_scan": {**json.loads(scan["data"]), "confirmed": bool(scan["confirmed"])},
            "current_height_cm": prof.get("height_cm"),
        }
    if doc["doc_type"] == "receta":
        row = db.execute("SELECT data FROM prescription_draft WHERE document_id = ?", (doc_id,)).fetchone()
        draft = json.loads(row["data"]) if row else None
        problems = [
            {"id": p["id"], "name": p["name"]}
            for p in db.execute(
                "SELECT id, name FROM problem WHERE person_id = ? ORDER BY name COLLATE NOCASE",
                (doc["person_id"],),
            )
        ]
        if draft:
            draft["suggested_problem_id"] = prescriptions.match_problem(draft.get("diagnosis"), problems)
        current = [
            r["name"]
            for r in db.execute(
                "SELECT name FROM medication WHERE person_id = ? AND active = 1", (doc["person_id"],)
            )
        ]
        return {
            "document": {
                k: doc[k]
                for k in (
                    "id",
                    "person_id",
                    "doc_type",
                    "title",
                    "collected_on",
                    "review_state",
                    "uploaded_at",
                )
            },
            "ai_saw": "",
            "redactions": {},
            "rows": [],
            "imaging": [],
            "prescription": draft,
            "problems": problems,
            "current_medications": current,
        }
    ex = db.execute("SELECT * FROM extraction WHERE document_id = ?", (doc_id,)).fetchone()
    rows = []
    open_ids = (
        {r["id"] for r in not_saved_rows(db, doc_id, True)} if doc["review_state"] == "revisada" else None
    )
    for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ? ORDER BY id", (doc_id,)):
        a = terminology.BY_KEY.get(r["analyte_key"] or "")
        problems = json.loads(r["problems"])
        if critical.check(r["analyte_key"], r["value_num"], r["qualifier"]):
            problems.append("valor_critico")
        rows.append(
            {
                **dict(r),
                "name": a.name if a else None,
                "unsaved": True if open_ids is None else r["id"] in open_ids,
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
        status = ranges.classify_text(txt or "", ref_text, terminology.expects_negative(analyte.key))
        return None, txt, analyte.unit, ranges.Ref(), status, None
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
    complete: bool = False,
) -> int:
    """Guarda en `observation` solo lo aceptado: filas del PDF (con correcciones o con el análisis que la
    persona indicó) y resultados que faltaban, escritos a mano y marcados como tales."""
    date.fromisoformat(collected_on)
    doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
    if complete and doc["review_state"] != "revisada":
        raise IngestError(409, "Solo se puede completar un estudio ya revisado.")
    if not complete and doc["review_state"] != "pendiente":
        raise IngestError(409, "Este estudio ya fue revisado.")
    rows = {r["id"]: r for r in db.execute("SELECT * FROM extraction_row WHERE document_id = ?", (doc_id,))}
    if complete:  # solo lo que aún no está guardado; lo guardado no se duplica ni se modifica
        open_ids = {r["id"] for r in not_saved_rows(db, doc_id, True)}
        rows = {i: r for i, r in rows.items() if i in open_ids}
        collected_on = doc["collected_on"]
    now = datetime.now().isoformat(timespec="seconds")
    common = {"collected_on": collected_on, "reviewer_id": reviewer_id, "now": now}
    saved = 0
    written: set[tuple] = set()
    for d in decisions:
        r = rows.get(d.get("row_id"))
        if r is None:
            raise IngestError(
                422, "Ese renglón ya está guardado o no existe." if complete else "Fila desconocida."
            )
        if d.get("ignore") and not d.get("accept"):
            db.execute("UPDATE extraction_row SET ignored = 1 WHERE id = ?", (r["id"],))
        if not d.get("accept"):
            continue
        key = d.get("analyte_key") or r["analyte_key"]
        same = (key, r["value_printed"], r["unit_printed"])
        if key and same in written:  # el mismo resultado impreso dos veces en el estudio: se guarda una vez
            continue
        written.add(same)
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
                status = ranges.classify_text(value_text, r["ref_printed"], terminology.expects_negative(key))
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
    if not complete:
        db.execute(
            "UPDATE document SET review_state = 'revisada', collected_on = ? WHERE id = ?",
            (collected_on, doc_id),
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
    for r in db.execute(
        "SELECT id, analyte_key, value_text, ref_printed, status FROM observation "
        "WHERE value_num IS NULL AND value_text IS NOT NULL"
    ).fetchall():
        status = ranges.classify_text(
            r["value_text"], r["ref_printed"], terminology.expects_negative(r["analyte_key"])
        )
        if status != r["status"]:
            db.execute("UPDATE observation SET status = ? WHERE id = ?", (status, r["id"]))
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
            "flag, confirmed_by, confirmed_at, name_source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                "manual"
                if study != (draft.get("study_name") or "").strip()
                else None,  # la persona lo corrigió
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
