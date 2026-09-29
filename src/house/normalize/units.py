"""Conversión de unidades determinista. La IA nunca convierte números."""

from __future__ import annotations

import re
import unicodedata


class UnknownUnit(ValueError):
    pass


def norm_unit(u: str | None) -> str:
    if u is None:
        return ""
    s = u.strip().replace("μ", "u").replace("µ", "u").lower().replace(" ", "")
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9/%]", "", s)


# (clave del analito, unidad de origen normalizada) -> (factor, desplazamiento, unidad canónica)
# valor_canónico = valor * factor + desplazamiento
_CONV: dict[tuple[str, str], tuple[float, float, str]] = {
    ("glucose", "mmol/l"): (18.016, 0.0, "mg/dL"),
    ("chol_total", "mmol/l"): (38.67, 0.0, "mg/dL"),
    ("ldl", "mmol/l"): (38.67, 0.0, "mg/dL"),
    ("hdl", "mmol/l"): (38.67, 0.0, "mg/dL"),
    ("triglycerides", "mmol/l"): (88.57, 0.0, "mg/dL"),
    ("creatinine", "umol/l"): (1 / 88.4, 0.0, "mg/dL"),
    ("vitamin_d", "nmol/l"): (1 / 2.496, 0.0, "ng/mL"),
    ("ferritin", "ug/l"): (1.0, 0.0, "ng/mL"),
    ("hba1c", "mmol/mol"): (0.09148, 2.152, "%"),  # IFCC -> NGSP
    ("hemoglobin", "g/l"): (0.1, 0.0, "g/dL"),
    ("crp_hs", "mg/dl"): (10.0, 0.0, "mg/L"),
}

_CANON = {
    "mg/dl": "mg/dL",
    "ng/ml": "ng/mL",
    "ng/dl": "ng/dL",
    "pg/ml": "pg/mL",
    "%": "%",
    "u/l": "U/L",
    "g/dl": "g/dL",
    "miu/l": "mIU/L",
    "uiu/ml": "mIU/L",
    "uui/ml": "mIU/L",  # µUI/mL (español)
    "mui/l": "mIU/L",
    "mg/l": "mg/L",
    "mmol/l": "mmol/L",
    "ug/dl": "µg/dL",
    "pg": "pg",
    "fl": "fL",
    "103/ul": "10^3/µL",
    "x103/ul": "10^3/µL",
    "106/ul": "10^6/µL",
    "x106/ul": "10^6/µL",
    "miles/ul": "10^3/µL",
    "millones/ul": "10^6/µL",
    "millones/ml": "10^6/mL",
    "millones": "10^6",
    "ml": "mL",
    "cm": "cm",
    "dias": "días",
    "ml/min/173m2": "mL/min/1.73m2",
}


# Unidades impresas que valen lo mismo que la canónica para ese analito: no es una conversión.
# Cocientes impresos con la unidad de sus operandos (índice aterogénico "6.3 mg/dL") y
# electrolitos monovalentes, donde 1 mEq/L = 1 mmol/L.
_SAME_VALUE = {
    ("chol_hdl_ratio", "mg/dl"),
    ("ldl_hdl_ratio", "mg/dl"),
    ("t3_uptake", "uct"),
    ("sodium", "meq/l"),
    ("potassium", "meq/l"),
    ("chloride", "meq/l"),
    ("co2_total", "meq/l"),
    ("egfr", "ml/min/172m2"),  # errata de Chopo 2018: "1.72m2" por "1.73m2"
}


def same_unit(key: str, unit_text: str | None, canonical_unit: str) -> bool:
    u = norm_unit(unit_text)
    return u == norm_unit(canonical_unit) or _CANON.get(u) == canonical_unit or (key, u) in _SAME_VALUE


def to_canonical(key: str, value: float, unit_text: str | None, canonical_unit: str) -> tuple[float, str]:
    """Devuelve (valor, unidad canónica). Lanza UnknownUnit si no sabe convertir."""
    u = norm_unit(unit_text)
    if (key, u) in _SAME_VALUE:
        return value, canonical_unit
    if u == "" and canonical_unit == "":
        return value, canonical_unit
    canon = _CANON.get(u)
    if canon == canonical_unit:
        return value, canonical_unit
    if u == "":
        raise UnknownUnit("sin unidad")
    conv = _CONV.get((key, u))
    if conv is None:
        raise UnknownUnit(f"{key}: no sé convertir '{unit_text}' a {canonical_unit}")
    factor, offset, target = conv
    if target != canonical_unit:
        raise UnknownUnit(f"{key}: la unidad canónica esperada es {canonical_unit}")
    return round(value * factor + offset, 2), canonical_unit
