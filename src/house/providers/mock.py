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
# Exige una unidad con "/" o "%" tras el valor, para no confundir números del nombre ("25-OH").
_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-zÁÉÍÓÚÑáéíóúñ]\S*(?:\s+\S+)*?)\s+(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>x?\s?10\^?\d+/[A-Za-zµμ]+|[A-Za-zµμ]+/[A-Za-zµμ]+(?:/[A-Za-zµμ]+)?|%|fL|pg)"
    r"(?:\s+(?P<ref>\S.*?))?\s*$"
)
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")


class BaselineRegexProvider:
    def __init__(self, name: str = "baseline-regex", **_: object) -> None:
        self.name = name

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        rows, collected = [], None
        for line in req.user.splitlines():
            if collected is None and re.search(r"toma|recolecci|muestra", line, re.I):
                m = _DATE.search(line)
                if m:
                    collected = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            m = _LINE.match(line)
            if m:
                rows.append(
                    {
                        "analyte_name": m.group("name").strip(),
                        "value_text": m.group("value"),
                        "unit_text": m.group("unit"),
                        "ref_text": (m.group("ref") or "").strip() or None,
                        "evidence": line.strip(),
                    }
                )
        data = {
            "document_type": "laboratorio" if rows else "otro",
            "collected_on": collected,
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
