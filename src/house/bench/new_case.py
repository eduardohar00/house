"""Crea un caso del banco de pruebas a partir de un PDF, todo en tu Mac.

Uso:
    python -m house.bench.new_case ~/Downloads/estudio.pdf --id 2026-09-quimica \
        --names "Nombre Apellido,Otro Nombre"

Crea bench/private/<id>/ con:
- document.txt : texto extraído del PDF (para que todos los proveedores vean lo mismo)
- document.pdf : copia del original, solo para que lo compares a la vista
- expected.json: PRELLENADO por una regla simple y marcado "verified": false
- profile.json : nombres y datos que NUNCA deben llegar a la IA (detectados + los que indiques)

El caso NO cuenta en el banco hasta que revises expected.json contra el PDF y cambies
"verified" a true. Si no lo revisas, el banco mediría contra respuestas sin comprobar.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from ..extract import Provenance, process
from ..privacy.anonymize import CURP, EMAIL, LABELED_ID, PHONE, RFC
from ..providers import LLMRequest
from ..providers.mock import BaselineRegexProvider
from ..schema import RawExtraction, extraction_json_schema


def extract_pdf_text(pdf: Path) -> str:
    try:
        import pdfplumber
    except ImportError:
        raise SystemExit('Falta pdfplumber. Instálalo con: python -m pip install -e ".[pdf]"') from None
    with pdfplumber.open(pdf) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages)


def detect_identifiers(text: str) -> list[str]:
    """Textos que parecen datos personales (para la lista 'forbidden'). Revísala a mano."""
    found: list[str] = []
    for rx in (CURP, RFC, EMAIL, PHONE):
        found += [m.group(0) for m in rx.finditer(text)]
    found += [m.group("val").strip() for m in LABELED_ID.finditer(text)]
    seen: set[str] = set()
    return [f for f in found if not (f in seen or seen.add(f))]


def prefill_expected(text: str) -> tuple[dict, list[str]]:
    """Devuelve (expected, líneas_sin_reconocer) usando la línea base sin IA y el mismo
    reconocimiento que el pipeline (catálogo, secciones, unidades)."""
    req = LLMRequest(task="extract", system="", user=text, schema=extraction_json_schema())
    data = BaselineRegexProvider().complete_json(req).data
    raw = RawExtraction.model_validate(data)
    results, unrecognized = [], []
    blocking = {
        Provenance.UNKNOWN_ANALYTE,
        Provenance.NOT_NUMERIC,
        Provenance.UNIT_PROBLEM,
        Provenance.SAME_IN_OTHER_UNIT,
    }
    for row in process(raw, text):
        if row.key is None or blocking & set(row.problems):
            unrecognized.append(row.evidence)
            continue
        value = row.value if row.value is not None else row.value_label
        results.append({"key": row.key, "value": value, "unit": row.unit})
    expected = {
        "verified": False,
        "_instrucciones": (
            "Compara cada fila con el PDF, corrige valores, agrega los analitos que falten (claves "
            "válidas en src/house/normalize/terminology.py) y cambia verified a true."
        ),
        "collected_on": data["collected_on"],
        "results": results,
    }
    return expected, unrecognized


def scaffold(case_dir: Path, text: str, names: list[str], pdf: Path | None = None) -> dict:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "document.txt").write_text(text, encoding="utf-8")
    if pdf:
        shutil.copyfile(pdf, case_dir / "document.pdf")
    expected, unrecognized = prefill_expected(text)
    expected["_lineas_sin_reconocer"] = unrecognized
    (case_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ids = detect_identifiers(text)
    profile = {"names": names, "forbidden": sorted(set(names) | set(ids))}
    (case_dir / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"prefilled": len(expected["results"]), "unrecognized": len(unrecognized), "ids": len(ids)}


def main() -> None:
    ap = argparse.ArgumentParser(prog="house.bench.new_case")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--id", required=True, help="nombre de la carpeta del caso")
    ap.add_argument("--names", default="", help="nombres a quitar, separados por coma")
    ap.add_argument("--root", type=Path, default=Path("bench/private"))
    args = ap.parse_args()
    text = extract_pdf_text(args.pdf)
    if len(re.sub(r"\s", "", text)) < 40:
        raise SystemExit(
            "Este PDF casi no tiene texto (parece un escaneo). Requiere OCR: llega en la Fase 1."
        )
    case_dir = args.root / args.id
    if case_dir.exists():
        raise SystemExit(f"Ya existe {case_dir}. Elige otro --id o bórrala a propósito.")
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    info = scaffold(case_dir, text, names, args.pdf)
    print(f"Caso creado en {case_dir}")
    print(f"- {info['prefilled']} filas prellenadas, {info['unrecognized']} líneas sin reconocer")
    print(f"- {info['ids']} identificadores personales detectados en profile.json (revísalos)")
    print("Siguiente: abre expected.json, compáralo con el PDF, corrígelo y pon verified: true.")
    if not names:
        print(
            "Aviso: no diste --names; agrega tu nombre en profile.json o se enviará al proveedor.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
