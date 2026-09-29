"""Lectura de tablas de un informe escaneado con visión de Claude (a petición de la persona).

Las tablas escaneadas (pruebas de alergia, baropodometría…) no se pueden reconstruir con reglas: el OCR
pierde las columnas y asociar mal un renglón con su valor sería peligroso. Aquí se le pide a Claude que las
transcriba tal cual desde la imagen; la persona las revisa contra el original. Se envía la imagen completa,
por eso solo ocurre cuando la persona lo pide.
"""

from __future__ import annotations

from typing import Any

from ..providers import LLMRequest, Router

MAX_PAGES = 4
MAX_TABLES = 10
MAX_ROWS = 300
MAX_CELL = 200

SYSTEM = """Transcribes tablas de informes médicos escaneados. Recibes imágenes de las páginas.

REGLAS
1. Copia exactamente lo impreso: nombres, números, símbolos y grados (por ejemplo «3+»). No interpretes,
   no corrijas ortografía, no conviertas unidades y no completes lo que no se lee.
2. Si una celda está vacía o no se lee con seguridad, usa null. Nunca adivines.
3. Conserva el orden de los renglones. Si la página tiene dos bloques de la misma tabla lado a lado, primero
   todo el bloque izquierdo y después todo el derecho, como renglones de una misma tabla.
4. Cada renglón debe tener exactamente tantos valores como columnas.
5. NO transcribas datos personales: nombre de la persona, dirección, teléfonos, fecha de nacimiento, números
   de historia o de folio, ni nombres de médicos.
6. En «notes» pon las leyendas, escalas y observaciones impresas (por ejemplo qué significa cada grado).
   Si no hay, null.
Responde solo con el JSON pedido."""

PROMPT = "Transcribe las tablas de este informe. Son {n} imagen(es), una por página, en orden."


def tables_schema() -> dict:
    nullable = {"type": ["string", "null"]}
    table = {
        "type": "object",
        "properties": {
            "caption": nullable,
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array", "items": nullable}},
        },
        "required": ["caption", "columns", "rows"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"tables": {"type": "array", "items": table}, "notes": nullable},
        "required": ["tables", "notes"],
        "additionalProperties": False,
    }


def _cell(v: Any) -> str | None:
    if v is None:
        return None
    t = " ".join(str(v).split())[:MAX_CELL]
    return t or None


def clean(data: dict) -> dict:
    """Lo que devuelve el modelo, ajustado: renglones del ancho de la tabla, sin vacíos y con topes."""
    tables = []
    for t in (data.get("tables") or [])[:MAX_TABLES]:
        cols = [_cell(c) or "" for c in t.get("columns") or []]
        if not cols:
            continue
        rows = []
        for r in (t.get("rows") or [])[:MAX_ROWS]:
            cells = [_cell(c) for c in (r or [])][: len(cols)]
            cells += [None] * (len(cols) - len(cells))
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({"caption": _cell(t.get("caption")), "columns": cols, "rows": rows})
    return {"tables": tables, "notes": _cell(data.get("notes")) if data.get("notes") else None}


def read_tables(router: Router, images: list[bytes]) -> dict:
    req = LLMRequest(
        task="extract",
        system=SYSTEM,
        user=PROMPT.format(n=len(images)),
        schema=tables_schema(),
        max_tokens=16000,
        images=tuple(images),
    )
    return clean(router.complete_json(req).data)
