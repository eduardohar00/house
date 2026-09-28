"""Catálogo semilla de analitos: nombre canónico, clave LOINC, unidad y alias en es/en.

Los códigos LOINC son la semilla del proyecto; verificarlos contra la versión oficial de LOINC
antes de usarlos para intercambio de datos. Lo que no coincide va a "otros" para revisión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Analyte:
    key: str
    name: str
    loinc: str
    unit: str
    group: str
    aliases: tuple[str, ...]


CATALOG: tuple[Analyte, ...] = (
    Analyte(
        "glucose",
        "Glucosa en ayunas",
        "2345-7",
        "mg/dL",
        "glucosa",
        ("glucosa", "glucosa en ayunas", "glucosa sérica", "glucose", "glucosa basal"),
    ),
    Analyte(
        "hba1c",
        "Hemoglobina glucosilada",
        "4548-4",
        "%",
        "glucosa",
        ("hemoglobina glucosilada", "hemoglobina glicosilada", "hba1c", "a1c"),
    ),
    Analyte(
        "chol_total",
        "Colesterol total",
        "2093-3",
        "mg/dL",
        "lipidos",
        ("colesterol total", "colesterol", "cholesterol total"),
    ),
    Analyte(
        "ldl",
        "Colesterol LDL",
        "13457-7",
        "mg/dL",
        "lipidos",
        ("colesterol ldl", "ldl", "ldl colesterol", "colesterol ldl calculado"),
    ),
    Analyte(
        "hdl", "Colesterol HDL", "2085-9", "mg/dL", "lipidos", ("colesterol hdl", "hdl", "hdl colesterol")
    ),
    Analyte(
        "triglycerides",
        "Triglicéridos",
        "2571-8",
        "mg/dL",
        "lipidos",
        ("trigliceridos", "triglicéridos", "triglycerides"),
    ),
    Analyte(
        "crp_hs",
        "Proteína C reactiva ultrasensible",
        "30522-7",
        "mg/L",
        "lipidos",
        ("proteina c reactiva ultrasensible", "pcr ultrasensible", "pcr us", "hs-crp"),
    ),
    Analyte(
        "alt",
        "ALT (TGP)",
        "1742-6",
        "U/L",
        "higado",
        ("alt", "tgp", "alt tgp", "tgp alt", "alanino aminotransferasa", "transaminasa glutamico piruvica"),
    ),
    Analyte("creatinine", "Creatinina", "2160-0", "mg/dL", "rinon", ("creatinina", "creatinine")),
    Analyte("hemoglobin", "Hemoglobina", "718-7", "g/dL", "sangre", ("hemoglobina", "hemoglobin", "hb")),
    Analyte("ferritin", "Ferritina", "2276-4", "ng/mL", "sangre", ("ferritina", "ferritin")),
    Analyte(
        "tsh", "TSH", "3016-3", "mIU/L", "tiroides", ("tsh", "hormona estimulante de tiroides", "tirotropina")
    ),
    Analyte(
        "vitamin_d",
        "Vitamina D",
        "1989-3",
        "ng/mL",
        "vitaminas",
        ("vitamina d", "vitamina d 25 oh", "25 oh vitamina d", "25-hidroxivitamina d", "vitamin d"),
    ),
)


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


_INDEX = {_norm(a): an for an in CATALOG for a in (*an.aliases, an.name)}
BY_KEY = {a.key: a for a in CATALOG}


def match_analyte(printed_name: str) -> Analyte | None:
    return _INDEX.get(_norm(printed_name))
