"""Recetas médicas: foto o PDF -> medicamentos propuestos, que la persona revisa antes de guardar.

Claude lee la imagen (letra impresa o a mano). La lectura es solo una propuesta: nada entra al expediente
hasta que la persona lo confirma junto al original. No se interpreta ni se sugiere ningún medicamento.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from ..providers import LLMRequest, Router

MAX_MEDS = 30

SYSTEM = """Transcribes recetas médicas. Recibes fotos o páginas escaneadas de una receta.

REGLAS
1. Copia lo escrito, sin interpretar: nombres de medicamentos, dosis, frecuencia y duración tal como aparecen.
   No agregues medicamentos que no estén escritos ni completes indicaciones que no se ven.
2. Una entrada por medicamento. Si dos medicamentos comparten renglón, sepáralos.
3. Si algo no se lee con seguridad, pon tu mejor lectura y marca legible = false. Si no hay dato, null.
4. Fecha de la receta en formato AAAA-MM-DD; null si no aparece o no es clara.
5. «prescriber» es el nombre del médico que firma; «diagnosis», el diagnóstico o motivo si está escrito.
6. NO transcribas otros datos personales (nombre de la persona, dirección, teléfonos, cédulas, folios).
7. En «notes» pon indicaciones generales (dieta, reposo, cita de control) si las hay; si no, null.
Responde solo con el JSON pedido."""

PROMPT = "Transcribe esta receta. Son {n} imagen(es), en orden."


def prescription_schema() -> dict:
    nullable = {"type": ["string", "null"]}
    med = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "dose": nullable,
            "frequency": nullable,
            "duration": nullable,
            "instructions": nullable,
            "legible": {"type": "boolean"},
        },
        "required": ["name", "dose", "frequency", "duration", "instructions", "legible"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "prescription_date": nullable,
            "prescriber": nullable,
            "diagnosis": nullable,
            "medications": {"type": "array", "items": med},
            "notes": nullable,
        },
        "required": ["prescription_date", "prescriber", "diagnosis", "medications", "notes"],
        "additionalProperties": False,
    }


def _s(v: Any, n: int = 200) -> str | None:
    if v is None:
        return None
    t = " ".join(str(v).split())[:n]
    return t or None


def clean(data: dict) -> dict:
    """Lo que devuelve el modelo, ajustado: textos cortos, fecha válida y solo medicamentos con nombre."""
    when = _s(data.get("prescription_date"), 10)
    try:
        when = date.fromisoformat(when).isoformat() if when else None
    except ValueError:
        when = None
    meds = []
    for m in (data.get("medications") or [])[:MAX_MEDS]:
        name = _s(m.get("name"), 120)
        if not name:
            continue
        meds.append(
            {
                "name": name,
                "dose": _s(m.get("dose")),
                "frequency": _s(m.get("frequency")),
                "duration": _s(m.get("duration")),
                "instructions": _s(m.get("instructions"), 400),
                "legible": bool(m.get("legible", True)),
            }
        )
    return {
        "prescription_date": when,
        "prescriber": _s(data.get("prescriber"), 120),
        "diagnosis": _s(data.get("diagnosis"), 200),
        "medications": meds,
        "notes": _s(data.get("notes"), 600),
    }


def read_prescription(router: Router, images: list[bytes]) -> dict:
    req = LLMRequest(
        task="extract",
        system=SYSTEM,
        user=PROMPT.format(n=len(images)),
        schema=prescription_schema(),
        max_tokens=8000,
        images=tuple(images),
    )
    return clean(router.complete_json(req).data)


def _plain(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def match_problem(diagnosis: str | None, problems: list[dict]) -> int | None:
    """Padecimiento ya registrado cuyo nombre coincide con el diagnóstico de la receta (o lo contiene)."""
    d = _plain(diagnosis or "")
    if len(d) < 4:
        return None
    for p in problems:
        n = _plain(p["name"])
        if n and (n == d or n in d or d in n):
            return p["id"]
    return None
