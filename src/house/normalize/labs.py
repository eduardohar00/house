"""Qué laboratorio emitió un estudio, leído del encabezado o pie del propio PDF (solo el nombre comercial).

Sirve para saber de dónde viene cada resultado y de quién es el rango de referencia que se muestra: dos
laboratorios pueden imprimir rangos distintos para el mismo análisis. Si no se reconoce, queda en blanco.
"""

from __future__ import annotations

import re

_LABS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Ángeles Lomas", re.compile(r"\bHA\s+LOMAS\b|hospital\s+[aá]ngeles\s+lomas", re.I)),
    ("Chopo", re.compile(r"\bchopo\b", re.I)),
    ("Lapi", re.compile(r"\blapi\b", re.I)),
)


def detect_lab(text: str) -> str | None:
    for name, rx in _LABS:
        if rx.search(text):
            return name
    return None
