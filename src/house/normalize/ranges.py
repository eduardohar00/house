"""Rangos de referencia: lectura del texto impreso y clasificación determinista."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

# "abnormal": resultado de texto que no coincide con la referencia ("Positivo" contra "Negativo").
Status = Literal["low", "ok", "high", "abnormal"]
_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


@dataclass(frozen=True)
class Ref:
    """Límites de referencia. "strict": el límite mismo queda fuera ("< 1": un 1 ya está fuera)."""

    low: float | None = None
    high: float | None = None
    low_strict: bool = False
    high_strict: bool = False


# Referencias por niveles («Recomendable < 100; Límite alto 130 - 159; Alto 160 - 189»): el rango del
# resultado es el nivel bueno, no el primer intervalo que aparezca (que suele ser un nivel de riesgo).
_GOOD_TIER = re.compile(r"^\s*(?:recomendable|ideal|deseable|normal|[oó]ptimo)\b\s*[:\-]?\s*", re.I)


def _good_tier(text: str) -> str:
    parts = [p for p in text.split(";") if p.strip()]
    if len(parts) > 1:
        for p in parts:
            if m := _GOOD_TIER.match(p):
                return p[m.end() :]
    return text


def parse_ref_full(text: str | None) -> Ref:
    """'70 - 99' -> 70 a 99 inclusive; '< 100' o 'menor a 100' -> hasta 100 sin incluirlo;
    '<= 100', '< = 100' o 'hasta 100' -> hasta 100 incluido; '> 40' -> más de 40; '40 o más' -> 40+."""
    if not text:
        return Ref()
    t = _good_tier(text).strip().lower().replace("–", "-").replace("—", "-")
    if m := re.search(_NUM + r"\s*(?:-|a|hasta)\s*" + _NUM, t):
        if "ausent" in t:  # «Ausentes ó 1 - 2»: ausente (0) también es lo esperado
            return Ref(0.0, _f(m[2]))
        return Ref(_f(m[1]), _f(m[2]))
    if t == "0":  # un conteo que debe ser cero (cilindros, cristales…)
        return Ref(0.0, 0.0)
    if m := re.search(r"(?:≤|<\s*=|menor\s+o\s+igual\s+(?:a|que)|hasta)\s*" + _NUM, t):
        return Ref(high=_f(m[1]))
    if m := re.search(r"(?:<|menor(?:\s+a|\s+que)?)\s*" + _NUM, t):
        return Ref(high=_f(m[1]), high_strict=True)
    if m := re.search(r"(?:≥|>\s*=|mayor\s+o\s+igual\s+(?:a|que))\s*" + _NUM, t):
        return Ref(low=_f(m[1]))
    if m := re.search(r"(?:>|mayor(?:\s+a|\s+que)?)\s*" + _NUM, t):
        return Ref(low=_f(m[1]), low_strict=True)
    if m := re.search(_NUM + r"\s*(?:o\s+m[aá]s|\+)", t):
        return Ref(low=_f(m[1]))
    return Ref()


def parse_ref(text: str | None) -> tuple[float | None, float | None]:
    r = parse_ref_full(text)
    return r.low, r.high


def classify(
    value: float,
    low: float | None,
    high: float | None,
    *,
    low_strict: bool = False,
    high_strict: bool = False,
) -> Status:
    if low is not None and (value <= low if low_strict else value < low):
        return "low"
    if high is not None and (value >= high if high_strict else value > high):
        return "high"
    return "ok"


def classify_censored(qualifier: str, bound: float, ref: Ref) -> Status | None:
    """Estado de un valor "menor que" (<) o "mayor que" (>) el límite de detección del método.

    "< 0.02" con rango 0 a 0.5: el valor real está entre 0 y 0.02, dentro de rango. Si con el límite no
    alcanza para saberlo, no hay estado (mejor que afirmar algo que el laboratorio no midió)."""
    if ref.low is None and ref.high is None:
        return None
    if qualifier.startswith("<"):
        if ref.low is not None and bound <= ref.low:
            return "low"
        return "ok" if ref.high is None or bound <= ref.high else None
    if ref.high is not None and bound >= ref.high:
        return "high"
    return "ok" if ref.low is None or bound >= ref.low else None


def classify_ref(value: float, ref: Ref) -> Status | None:
    """Estado contra un rango; sin ningún límite no hay estado (no es lo mismo que "en rango")."""
    if ref.low is None and ref.high is None:
        return None
    return classify(value, ref.low, ref.high, low_strict=ref.low_strict, high_strict=ref.high_strict)


def norm_text_result(s: str) -> str:
    """Forma comparable de un resultado de texto: sin acentos, mayúsculas, plural ni género
    ("Ausentes" = "ausente", "Positiva" = "positivo", "No Reactivo" = "no reactivo")."""
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    words = re.sub(r"[^a-z0-9]+", " ", s).split()
    return " ".join(re.sub(r"[ao]$", "", re.sub(r"s$", "", w)) if len(w) > 3 else w for w in words)


_NEGATIVE = {
    norm_text_result(t) for t in ("no detectado", "negativo", "no reactivo", "ausente", "no se detecta")
}
_POSITIVE = {norm_text_result(t) for t in ("detectado", "positivo", "reactivo", "presente")}


def _is_pointer(ref_text: str) -> bool:
    """«Ver Anexo», «Ver nota»…: el laboratorio remite a otro lado; no dice cuál es el resultado esperado."""
    return norm_text_result(ref_text).startswith("ver ")


def classify_text(value: str, ref_text: str | None, expect_negative: bool = False) -> Status | None:
    """'ok' si el resultado es una de las opciones normales de la referencia; si no, 'abnormal'.

    Sin referencia utilizable (vacía o «Ver Anexo») no hay estado, salvo en pruebas de presencia de un
    microorganismo (`expect_negative`): ahí lo esperado es que no se detecte, se imprima o no.
    """
    options: set[str] = set()
    if ref_text and not _is_pointer(ref_text):
        # Quita unidades: "Leu/µL NEGATIVO" -> "NEGATIVO"; "/ Campo NEGATIVO" -> "NEGATIVO".
        without_units = re.sub(r"\S*/\S+|/\s*\S+", " ", ref_text)
        options = {
            norm_text_result(p)
            for p in re.split(r"\s+(?:ó|o)\s+|\s+[-–]\s+|,", without_units)
            if p.strip() and not re.search(r"\d", p)
        }
        options.discard("")
    if options:
        return "ok" if norm_text_result(value) in options else "abnormal"
    if expect_negative:
        v = norm_text_result(value)
        return "ok" if v in _NEGATIVE else "abnormal" if v in _POSITIVE else None
    return None


def deviation(value: float, low: float | None, high: float | None) -> float:
    """Distancia relativa fuera del rango (0 si está dentro)."""
    if high is not None and value > high:
        return (value - high) / high
    if low is not None and value < low:
        return (low - value) / low
    return 0.0
