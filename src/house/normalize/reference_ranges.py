"""Rangos de referencia generales por sexo y edad, para cuando el laboratorio no imprime uno.

Orden de prioridad (ver summary.py): 1) el rango impreso por el laboratorio (ya considera sexo y edad),
2) el del estudio anterior del mismo análisis y unidad, 3) esta tabla.

PROVISIONAL: son rangos de uso común en adultos, sin verificar contra una fuente citable, y deben revisarse
con un médico antes de confiar en ellos (ver CLAUDE.md). No se aplican a menores de 18 años: los rangos
pediátricos dependen de la edad y House solo usa los que imprime el laboratorio. No modelan embarazo,
menopausia ni condiciones especiales. Los límites están en la unidad canónica del catálogo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .ranges import Ref

ADULT_AGE = 18


@dataclass(frozen=True)
class Rule:
    low: float | None = None
    high: float | None = None
    low_strict: bool = False  # "> 40": el 40 mismo queda fuera
    high_strict: bool = False  # "< 150": el 150 mismo queda fuera
    sex: str | None = None  # "M", "F" o None = ambos
    min_age: int = ADULT_AGE
    max_age: int | None = None


def _both(low: float | None = None, high: float | None = None, **kw: bool) -> list[Rule]:
    return [Rule(low, high, **kw)]


def _by_sex(m: tuple[float, float], f: tuple[float, float]) -> list[Rule]:
    return [Rule(*m, sex="M"), Rule(*f, sex="F")]


RANGES: dict[str, list[Rule]] = {
    # Distintos por sexo
    "hemoglobin": _by_sex((13.5, 17.5), (12.0, 15.5)),  # g/dL
    "hematocrit": _by_sex((41, 53), (36, 46)),  # %
    "rbc": _by_sex((4.5, 5.9), (4.1, 5.1)),  # 10^6/µL
    "ferritin": _by_sex((24, 336), (11, 307)),  # ng/mL
    "creatinine": _by_sex((0.74, 1.35), (0.59, 1.04)),  # mg/dL
    "uric_acid": _by_sex((3.7, 8.0), (2.7, 6.3)),  # mg/dL
    "ggt": _by_sex((8, 61), (5, 36)),  # U/L
    "alt": _by_sex((7, 55), (7, 45)),  # U/L
    "ast": _by_sex((8, 48), (8, 43)),  # U/L
    "alp": _by_sex((40, 129), (35, 104)),  # U/L
    "iron": _by_sex((65, 175), (50, 170)),  # µg/dL
    "hdl": [Rule(low=40, low_strict=True, sex="M"), Rule(low=50, low_strict=True, sex="F")],  # mg/dL
    # Por edad (solo hombres): antígeno prostático, ng/mL
    "psa_total": [
        Rule(0, 2.5, sex="M", max_age=49),
        Rule(0, 3.5, sex="M", min_age=50, max_age=59),
        Rule(0, 4.5, sex="M", min_age=60, max_age=69),
        Rule(0, 6.5, sex="M", min_age=70),
    ],
    # Iguales para todos los adultos
    "glucose": _both(70, 99),  # mg/dL, en ayunas
    "hba1c": _both(high=5.7, high_strict=True),  # %
    "triglycerides": _both(high=150, high_strict=True),  # mg/dL
    "chol_total": _both(high=200, high_strict=True),  # mg/dL
    "ldl": _both(high=100, high_strict=True),  # mg/dL
    "non_hdl": _both(high=130, high_strict=True),  # mg/dL
    "platelets": _both(150, 450),  # 10^3/µL
    "wbc": _both(4.5, 11.0),  # 10^3/µL
    "tsh": _both(0.4, 4.0),  # mIU/L
    "vitamin_d": _both(30, 100),  # ng/mL
    "sodium": _both(135, 145),  # mmol/L
    "potassium": _both(3.5, 5.1),  # mmol/L
    "chloride": _both(98, 107),  # mmol/L
    "calcium": _both(8.6, 10.2),  # mg/dL
    "bilirubin_total": _both(0.1, 1.2),  # mg/dL
    "bun": _both(7, 20),  # mg/dL
}

_WHO = {"M": "hombres adultos", "F": "mujeres adultas", None: "adultos"}


def age_on(birth_date: str | None, on: str | date | None) -> int | None:
    """Años cumplidos a la fecha indicada (None si falta alguna fecha o no es válida)."""
    try:
        b = date.fromisoformat(birth_date or "")
        d = on if isinstance(on, date) else date.fromisoformat(on or "")
    except ValueError:
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def general_ref(key: str, sex: str | None, age: int | None) -> tuple[Ref, str] | None:
    """(rango, descripción) para ese análisis, sexo y edad; None si no hay o no aplica (menores de 18)."""
    if age is None or age < ADULT_AGE:
        return None
    for r in RANGES.get(key, ()):
        if r.sex not in (None, sex) or age < r.min_age or (r.max_age is not None and age > r.max_age):
            continue
        who = _WHO[r.sex if r.sex == sex else None]
        return Ref(
            r.low, r.high, r.low_strict, r.high_strict
        ), f"Referencia general para {who} (no es del laboratorio)"
    return None
