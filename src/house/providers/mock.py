"""Proveedor sin IA para pruebas y como línea base del banco de pruebas.

Extrae filas con una expresión regular sobre líneas tipo:
    Glucosa 92 mg/dL 70 - 99   (con uno o varios espacios entre columnas)
Sirve para (1) correr todo sin red ni costo y (2) medir cuánto aporta un modelo real
frente a una regla simple.
"""

from __future__ import annotations

import re
import time

from .base import LLMRequest, LLMResponse

# Independiente del espaciado (los PDF suelen colapsar las columnas a un solo espacio).
# Acepta dos órdenes de columnas: "valor unidad referencia" y "valor referencia unidad" (Chopo).
# Sin unidad impresa exige un intervalo de referencia; así no confunde números del nombre ("25-OH")
# ni leyendas ("ALTO 200 - 499"). El "*" tras un valor fuera de rango se tolera.
_UNIT = r"x?\s?10\^?\d+/[A-Za-zµμ]+|[A-Za-zµμ]+/[A-Za-zµμ0-9.]+(?:/[A-Za-zµμ0-9.]+)?|%|[fF][lL]|pg|UCT"
_WORD_UNIT = r"días|dias|mL|cm|millones"
_REF = r"[<>]\s*=?\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?"
_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-zÁÉÍÓÚÑáéíóúñ]\S*(?:\s+\S+)*?)\s+(?P<value>\d+(?:[.,]\d+)?)(?:\s*\*)?"
    rf"(?:\s*(?P<unit>{_UNIT})(?:\s+(?P<ref>\S.*?))?"
    rf"|\s+(?P<ref_only>{_REF})(?:\s+(?P<unit_after>{_UNIT}|{_WORD_UNIT})(?:\s*\(.*\))?)?)\s*$"
)
# Unidad sola en el renglón siguiente (Chopo parte "4.70-5.80 / millones/µL").
_UNIT_LINE = re.compile(rf"^\s*(?:{_UNIT}|{_WORD_UNIT})\s*$")
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


class BaselineRegexProvider:
    def __init__(self, name: str = "baseline-regex", **_: object) -> None:
        self.name = name

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        rows, collected, registered = [], None, None
        lines = req.user.splitlines()
        for i, line in enumerate(lines):
            if collected is None and re.search(r"toma|recolecci|muestra", line, re.I):
                m = _DATE.search(line)
                if m:
                    collected = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            if registered is None and re.search(r"registro", line, re.I):
                m = _DATE.search(line)
                if m:
                    registered = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            m = _LINE.match(line)
            if m:
                unit = m.group("unit") or m.group("unit_after")
                if unit is None and i + 1 < len(lines) and _UNIT_LINE.match(lines[i + 1]):
                    unit = lines[i + 1].strip()
                rows.append(
                    {
                        "analyte_name": m.group("name").strip(),
                        "value_text": m.group("value"),
                        "unit_text": unit,
                        "ref_text": (m.group("ref") or m.group("ref_only") or "").strip() or None,
                        "evidence": line.strip(),
                    }
                )
        data = {
            "document_type": "laboratorio" if rows else "otro",
            "collected_on": collected or registered,
            "lab_name": None,
            "rows": rows,
        }
        return LLMResponse(
            data=data,
            provider=self.name,
            model="regex",
            cost_usd=0.0,
            latency_s=time.monotonic() - t0,
        )
