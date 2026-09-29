"""Valores críticos: resultados tan alejados de lo normal que conviene avisar a un médico pronto.

Son los límites de «valor de pánico» que suelen usar los laboratorios para adultos. Provisionales: deben
revisarse con un médico (ver CLAUDE.md). No son un diagnóstico y no aplican igual a niños ni a recién nacidos.
Los límites están en la unidad canónica del catálogo (la misma en que House guarda cada resultado).
"""

from __future__ import annotations

# clave: (crítico por debajo de, crítico por encima de). None = no aplica de ese lado.
LIMITS: dict[str, tuple[float | None, float | None]] = {
    "glucose": (40, 500),  # mg/dL
    "potassium": (2.5, 6.5),  # mmol/L
    "sodium": (120, 160),  # mmol/L
    "chloride": (80, 125),  # mmol/L
    "co2_total": (10, 40),  # mmol/L
    "calcium": (6.5, 13.0),  # mg/dL
    "magnesium": (1.0, 4.7),  # mg/dL
    "phosphorus": (1.0, None),  # mg/dL
    "hemoglobin": (7.0, 20.0),  # g/dL
    "hematocrit": (20, 60),  # %
    "platelets": (20, 1000),  # 10^3/µL
    "wbc": (2.0, 30.0),  # 10^3/µL
    "neut_abs": (0.5, None),  # 10^3/µL
    "inr": (None, 5.0),
    "aptt": (None, 100),  # s
    "fibrinogen": (100, None),  # mg/dL
    "bun": (None, 100),  # mg/dL
    "bilirubin_total": (None, 15.0),  # mg/dL
    "alt": (None, 1000),  # U/L
    "ast": (None, 1000),  # U/L
    "triglycerides": (None, 1000),  # mg/dL
    "procalcitonin": (None, 10.0),  # ng/mL
}

MESSAGE = (
    "Un resultado así de alejado de lo normal conviene comentarlo con un médico pronto. "
    "Antes, confirma en el original que el valor esté bien leído. Esto no es un diagnóstico."
)


def check(analyte_key: str | None, value: float | None, qualifier: str | None = None) -> dict | None:
    """Si el valor rebasa un límite crítico: {"side": "low"|"high", "limit": número}. Si no, None."""
    if value is None or qualifier or analyte_key not in LIMITS:
        return None
    low, high = LIMITS[analyte_key]
    if low is not None and value < low:
        return {"side": "low", "limit": low}
    if high is not None and value > high:
        return {"side": "high", "limit": high}
    return None
