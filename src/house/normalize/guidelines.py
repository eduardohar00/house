"""Metas de guías clínicas por marcador (distintas del rango de referencia del laboratorio).

El rango del laboratorio dice qué es habitual en personas sanas con ese método; una meta de guía dice cuánto
conviene tener para cuidar el riesgo cardiovascular, y puede ser más exigente. House las muestra como una
segunda marca en la gráfica y NUNCA cambia con ellas el estado («Atención/Vigilar») ni los rangos
del laboratorio.

PROVISIONAL: cada meta cita su fuente y debe confirmarse con un médico. Empezamos con un solo marcador
(colesterol total) para ver cómo se ve y se entiende antes de agregar más.
Los límites están en la unidad canónica del catálogo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Zone:
    name: str
    low: float | None = None  # incluido
    high: float | None = None  # excluido


@dataclass(frozen=True)
class Guideline:
    key: str
    goal_high: float  # la meta: «menos de»
    zones: tuple[Zone, ...]
    source: str
    url: str
    min_age: int
    sex: str | None = None  # None = igual para ambos sexos
    notes: tuple[str, ...] = field(default_factory=tuple)


GUIDELINES: dict[str, Guideline] = {
    "chol_total": Guideline(
        key="chol_total",
        goal_high=200,
        zones=(
            Zone("Deseable", None, 200),
            Zone("Riesgo (límite alto)", 200, 240),
            Zone("Alto", 240, None),
        ),
        source="NOM-037-SSA2-2012 (dislipidemias, México)",
        url="https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S0188-21982012000300001",
        min_age=20,
        notes=(
            "Son los mismos cortes que NCEP ATP III (EE. UU.) y valen igual para hombres y mujeres adultos.",
            "Las guías europeas y estadounidenses actuales basan las metas en el colesterol LDL y no-HDL, "
            "según tu riesgo cardiovascular; el colesterol total solo es una referencia general.",
        ),
    ),
}


def zone_of(g: Guideline, value: float) -> str:
    for z in g.zones:
        if (z.low is None or value >= z.low) and (z.high is None or value < z.high):
            return z.name
    return ""


def for_person(profile: dict, today: date | None = None) -> dict[str, dict]:
    """Metas que aplican a esta persona (por edad y sexo), listas para la pantalla."""
    today = today or date.today()
    try:
        born = date.fromisoformat(profile.get("birth_date") or "")
    except ValueError:
        return {}
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    out: dict[str, dict] = {}
    for key, g in GUIDELINES.items():
        if age < g.min_age or (g.sex and g.sex != profile.get("sex")):
            continue
        out[key] = {
            "goal_high": g.goal_high,
            "zones": [{"name": z.name, "low": z.low, "high": z.high} for z in g.zones],
            "source": g.source,
            "url": g.url,
            "notes": list(g.notes),
        }
    return out
