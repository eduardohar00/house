"""Catálogo semilla de analitos: nombre canónico, clave LOINC, unidad y alias en es/en.

Los códigos LOINC son la semilla del proyecto; verificarlos contra la versión oficial de LOINC
antes de usarlos para intercambio de datos. Lo que no coincide va a "otros" para revisión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import units


@dataclass(frozen=True)
class Analyte:
    key: str
    name: str
    loinc: str
    unit: str
    group: str
    aliases: tuple[str, ...]


_BASE: tuple[Analyte, ...] = (
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
        (
            "colesterol ldl",
            "ldl",
            "ldl colesterol",
            "colesterol ldl calculado",
            "colesterol de baja densidad ldl",
            "colesterol ldl directo",
        ),
    ),
    Analyte(
        "hdl",
        "Colesterol HDL",
        "2085-9",
        "mg/dL",
        "lipidos",
        ("colesterol hdl", "hdl", "hdl colesterol", "colesterol de alta densidad hdl"),
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
        (
            "alt",
            "tgp",
            "alt tgp",
            "tgp alt",
            "alanino aminotransferasa",
            "transaminasa glutamico piruvica",
            "transaminasa glutamico piruvica alt",
        ),
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


def _a(key, name, loinc, unit, group, *aliases):
    return Analyte(key, name, loinc, unit, group, tuple(aliases))


# Ampliación a partir de estudios reales (solo nombres y unidades). Los códigos LOINC en blanco
# están por completar: no se inventan.
_EXTRA: tuple[Analyte, ...] = (
    _a("albumin", "Albúmina", "1751-7", "g/dL", "higado", "albumina"),
    _a("albumin_urine", "Albúmina (mg/L)", "", "mg/L", "orina"),
    _a("amylase", "Amilasa", "1798-8", "U/L", "pancreas", "amilasa en suero", "amilasa"),
    _a("lipase", "Lipasa", "3040-3", "U/L", "pancreas", "lipasa"),
    _a("bilirubin_total", "Bilirrubina total", "1975-2", "mg/dL", "higado", "bilirrubina total"),
    _a("bilirubin_direct", "Bilirrubina directa", "1968-7", "mg/dL", "higado", "bilirrubina directa"),
    _a("bilirubin_indirect", "Bilirrubina indirecta", "1971-1", "mg/dL", "higado", "bilirrubina indirecta"),
    _a(
        "ast",
        "AST (TGO)",
        "1920-8",
        "U/L",
        "higado",
        "ast",
        "tgo",
        "ast tgo",
        "tgo ast",
        "transaminasa glutamico oxalacetica",
        "transaminasa glutamico oxalacetica ast",
    ),
    _a(
        "alp",
        "Fosfatasa alcalina",
        "6768-6",
        "U/L",
        "higado",
        "fosfatasa alcalina total",
        "fosfatasa alcalina",
    ),
    _a(
        "ggt",
        "GGT",
        "2324-2",
        "U/L",
        "higado",
        "gamaglutamil transpeptidasa ggt",
        "gamma glutamil transpeptidasa",
        "ggt",
    ),
    _a("ldh", "Deshidrogenasa láctica", "2532-0", "U/L", "otros", "deshidrogenasa lactica", "dhl"),
    _a(
        "cpk",
        "Creatinfosfoquinasa (CPK)",
        "2157-6",
        "U/L",
        "otros",
        "creatinfosfoquinasa cpk",
        "cpk",
        "ck total",
    ),
    _a("globulin", "Globulina", "10834-0", "g/dL", "higado", "globulina"),
    _a("total_lipids", "Lípidos totales", "", "mg/dL", "lipidos", "lipidos totales"),
    _a(
        "non_hdl",
        "Colesterol no-HDL",
        "43396-1",
        "mg/dL",
        "lipidos",
        "no hdl colesterol",
        "colesterol no hdl",
    ),
    _a(
        "bun",
        "Nitrógeno ureico (BUN)",
        "3094-0",
        "mg/dL",
        "rinon",
        "nitrogeno ureico bun",
        "nitrogeno ureico",
        "bun",
    ),
    _a("urea", "Urea", "3091-6", "mg/dL", "rinon", "urea serica", "urea"),
    _a("sodium", "Sodio", "2951-2", "mmol/L", "electrolitos", "sodio"),
    _a("potassium", "Potasio", "2823-3", "mmol/L", "electrolitos", "potasio"),
    _a("co2_total", "CO2 total", "2028-9", "mmol/L", "electrolitos", "co2 total", "bicarbonato"),
    _a("phosphorus", "Fósforo", "2777-1", "mg/dL", "electrolitos", "fosforo"),
    _a("magnesium", "Magnesio", "19123-9", "mg/dL", "electrolitos", "magnesio"),
    _a("iron", "Hierro sérico", "2498-4", "µg/dL", "sangre", "hierro serico", "hierro"),
    _a("wbc", "Leucocitos", "6690-2", "10^3/µL", "sangre", "leucocitos"),
    _a("rbc", "Eritrocitos", "789-8", "10^6/µL", "sangre", "eritrocitos"),
    _a("hematocrit", "Hematocrito", "4544-3", "%", "sangre", "hematocrito"),
    _a(
        "mch",
        "Hemoglobina corpuscular media",
        "785-6",
        "pg",
        "sangre",
        "hemoglobina corpuscular media",
        "hcm",
    ),
    _a(
        "mchc",
        "Concentración media de hemoglobina",
        "786-4",
        "g/dL",
        "sangre",
        "concentracion media de hemoglobina",
        "chcm",
    ),
    _a(
        "rdw",
        "Amplitud de distribución eritrocitaria",
        "788-0",
        "%",
        "sangre",
        "amplitud de la distribucion eritrocitaria",
        "amplitud de distribucion eritrocitaria",
        "rdw",
    ),
    _a("mcv", "Volumen corpuscular medio", "787-2", "fL", "sangre", "volumen corpuscular medio", "vcm"),
    _a("platelets", "Plaquetas", "777-3", "10^3/µL", "sangre", "plaquetas"),
    _a("neut_seg_pct", "Neutrófilos segmentados %", "", "%", "diferencial", "neutrofilos segmentados"),
    _a(
        "neut_seg_abs",
        "Neutrófilos segmentados abs.",
        "",
        "10^3/µL",
        "diferencial",
        "neutrofilos segmentados absolutos",
    ),
    _a("neut_band_pct", "Neutrófilos en banda %", "763-3", "%", "diferencial", "neutrofilos en banda"),
    _a(
        "neut_band_abs",
        "Neutrófilos en banda abs.",
        "764-1",
        "10^3/µL",
        "diferencial",
        "neutrofilos en banda absolutos",
    ),
    _a("lymph_pct", "Linfocitos %", "736-9", "%", "diferencial", "linfocitos"),
    _a("lymph_abs", "Linfocitos abs.", "731-0", "10^3/µL", "diferencial", "linfocitos absolutos"),
    _a("mono_pct", "Monocitos %", "5905-5", "%", "diferencial", "monocitos"),
    _a("mono_abs", "Monocitos abs.", "742-7", "10^3/µL", "diferencial", "monocitos absolutos"),
    _a("eos_pct", "Eosinófilos %", "713-8", "%", "diferencial", "eosinofilos"),
    _a("eos_abs", "Eosinófilos abs.", "711-2", "10^3/µL", "diferencial", "eosinofilos absolutos"),
    _a("baso_pct", "Basófilos %", "706-2", "%", "diferencial", "basofilos"),
    _a("baso_abs", "Basófilos abs.", "704-7", "10^3/µL", "diferencial", "basofilos absolutos"),
    _a("uric_acid", "Ácido úrico", "3084-1", "mg/dL", "rinon", "acido urico"),
    _a(
        "protein_total",
        "Proteínas totales",
        "2885-2",
        "g/dL",
        "higado",
        "proteinas totales",
        "proteina total",
    ),
    _a("calcium", "Calcio", "17861-6", "mg/dL", "electrolitos", "calcio", "calcio serico"),
    _a("chloride", "Cloro", "2075-0", "mmol/L", "electrolitos", "cloro", "cloruro", "cloruros"),
    _a(
        "vldl",
        "Colesterol VLDL",
        "",
        "mg/dL",
        "lipidos",
        "colesterol de muy baja densidad vldl",
        "colesterol vldl",
        "vldl",
    ),
    _a("mpv", "Volumen plaquetario medio", "32623-1", "fL", "sangre", "volumen plaquetario medio", "vpm"),
    # Cocientes y análisis de orina sin unidad: unidad canónica "".
    _a("ag_ratio", "Relación albúmina/globulina", "1759-0", "", "higado", "relacion a g"),
    _a("chol_hdl_ratio", "Índice aterogénico (CT/HDL)", "9830-1", "", "lipidos", "indice aterogenico"),
    _a("ldl_hdl_ratio", "Índice LDL/HDL", "", "", "lipidos", "indice ldl hdl", "relacion ldl hdl"),
    _a(
        "urine_sg", "Gravedad específica (orina)", "", "", "orina", "gravedad especifica", "densidad urinaria"
    ),
    _a("urine_ph", "pH (orina)", "", "", "orina", "ph", "ph urinario"),
)

CATALOG: tuple[Analyte, ...] = _BASE + _EXTRA

# Mismo nombre impreso, distinto analito según la unidad (p. ej. albúmina en suero vs. mg/L).
_UNIT_VARIANTS = {("albumin", "mg/l"): "albumin_urine"}


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


_INDEX = {_norm(a): an for an in CATALOG for a in (*an.aliases, an.name)}
BY_KEY = {a.key: a for a in CATALOG}


def match_analyte(printed_name: str, unit: str | None = None) -> Analyte | None:
    found = _INDEX.get(_norm(printed_name))
    if found and unit:
        variant = _UNIT_VARIANTS.get((found.key, units.norm_unit(unit)))
        if variant:
            return BY_KEY[variant]
    return found
