"""Rangos de referencia: lectura del texto impreso y clasificación determinista."""

from __future__ import annotations

import re
from typing import Literal

Status = Literal["low", "ok", "high"]
_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


def parse_ref(text: str | None) -> tuple[float | None, float | None]:
    """'70 - 99' -> (70, 99); '< 100' o 'menor a 100' -> (None, 100); '40 o más' -> (40, None)."""
    if not text:
        return None, None
    t = text.strip().lower().replace("–", "-").replace("—", "-")
    if m := re.search(_NUM + r"\s*(?:-|a|hasta)\s*" + _NUM, t):
        return _f(m[1]), _f(m[2])
    if m := re.search(r"(?:<|≤|<=|menor(?:\s+a|\s+que)?|hasta)\s*" + _NUM, t):
        return None, _f(m[1])
    if m := re.search(r"(?:>|≥|>=|mayor(?:\s+a|\s+que)?)\s*" + _NUM, t):
        return _f(m[1]), None
    if m := re.search(_NUM + r"\s*(?:o\s+m[aá]s|\+)", t):
        return _f(m[1]), None
    return None, None


def classify(value: float, low: float | None, high: float | None) -> Status:
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "ok"


def deviation(value: float, low: float | None, high: float | None) -> float:
    """Distancia relativa fuera del rango (0 si está dentro)."""
    if high is not None and value > high:
        return (value - high) / high
    if low is not None and value < low:
        return (low - value) / low
    return 0.0
