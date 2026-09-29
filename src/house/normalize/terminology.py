"""Catálogo semilla de analitos: nombre canónico, clave LOINC, unidad y alias en es/en.

Los códigos LOINC son la semilla del proyecto; verificarlos contra la versión oficial de LOINC
antes de usarlos para intercambio de datos. Lo que no coincide va a "otros" para revisión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from . import units


@dataclass(frozen=True)
class Analyte:
    key: str
    name: str
    loinc: str
    unit: str
    group: str
    aliases: tuple[str, ...]
    # "num": solo cifras; "qual": solo texto ("Negativo", "Ausentes"); "mixed": ambos (tiras de orina).
    kind: str = "num"


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
        "tsh",
        "TSH",
        "3016-3",
        "mIU/L",
        "tiroides",
        ("tsh", "hormona estimulante de tiroides", "hormona estimulante de tiroides tsh", "tirotropina"),
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
    _a("t3_total", "T3 total", "3053-6", "ng/dL", "tiroides", "t3 total", "triyodotironina total"),
    _a("t3_free", "T3 libre", "3051-0", "pg/mL", "tiroides", "t3 libre", "triyodotironina libre"),
    _a("t3_uptake", "Captación de T3", "", "", "tiroides", "t3 captacion", "captacion de t3"),
    _a(
        "t4_total",
        "T4 total",
        "3026-2",
        "µg/dL",
        "tiroides",
        "tiroxina t4 total",
        "t4 total",
        "tiroxina total",
    ),
    _a("t4_free", "T4 libre", "3024-7", "ng/dL", "tiroides", "t4 libre", "tiroxina libre"),
    _a("fti", "Índice de tiroxina libre", "", "µg/dL", "tiroides", "indice de tiroxina libre", "t7"),
    _a("pbi", "Yodo proteico hormonal", "", "µg/dL", "tiroides", "yodo proteico hormonal"),
)

# Chopo y otros laboratorios (solo nombres y unidades). LOINC en blanco: por completar.
_CHOPO: tuple[Analyte, ...] = (
    _a("neut_pct", "Neutrófilos %", "", "%", "diferencial", "neutrofilos"),
    _a("neut_abs", "Neutrófilos abs.", "", "10^3/µL", "diferencial", "neutrofilos absolutos"),
    _a(
        "rdw_sd",
        "Ancho de distribución eritrocitaria (SD)",
        "",
        "fL",
        "sangre",
        "ancho de distrib de eritrocitos sd",
    ),
    _a(
        "bun_creat_ratio",
        "Relación BUN/creatinina",
        "",
        "",
        "rinon",
        "relacion bun creat",
        "relacion bun creatinina",
    ),
    _a(
        "egfr",
        "Tasa de filtración glomerular estimada",
        "",
        "mL/min/1.73m2",
        "rinon",
        "tasa de filtracion glomerular estima",
        "tasa de filtracion glomerular estimada",
        "tfge",
    ),
    _a("sd_ldl", "LDL pequeñas y densas (sd LDL)", "", "", "lipidos", "sd ldl"),
    _a("phospholipids", "Fosfolípidos", "", "mg/dL", "lipidos", "fosfolipidos en suero", "fosfolipidos"),
    _a("ast_alt_ratio", "Relación AST/ALT", "", "", "higado", "relacion ast alt"),
    _a("uibc", "Capacidad latente de fijación de hierro (UIBC)", "", "µg/dL", "sangre", "uibc"),
    _a(
        "tibc",
        "Capacidad total de fijación de hierro",
        "",
        "µg/dL",
        "sangre",
        "captacion de hierro",
        "capacidad total de fijacion de hierro",
        "tibc",
    ),
    _a("iron_sat", "Saturación de hierro", "", "%", "sangre", "porcentaje de saturacion de hierro"),
    _a("igg", "Inmunoglobulina G", "", "mg/dL", "inmunologia", "inmunoglobulina g", "igg"),
    _a("iga", "Inmunoglobulina A", "", "mg/dL", "inmunologia", "inmunoglobulina a", "iga"),
    _a("igm", "Inmunoglobulina M", "", "mg/dL", "inmunologia", "inmunoglobulina m", "igm"),
    _a("cea", "Antígeno carcinoembrionario", "", "ng/mL", "marcadores", "antigeno carcinoembrionario"),
    _a(
        "psa_total",
        "Antígeno prostático específico total",
        "",
        "ng/mL",
        "marcadores",
        "antigeno prostatico especifico total",
        "psa total",
    ),
    # Espermatobioscopía. "Volumen" y "pH" sin contexto se confunden con otros estudios: sin alias.
    _a("semen_abstinence", "Días de abstinencia", "", "días", "seminal", "dias de abstinencia"),
    _a("semen_volume", "Volumen seminal", "", "mL", "seminal"),
    _a("semen_viscosity", "Viscosidad seminal", "", "cm", "seminal", "viscosidad"),
    _a("semen_ph", "pH seminal", "", "", "seminal"),
    _a(
        "semen_conc",
        "Concentración de espermatozoides",
        "",
        "10^6/mL",
        "seminal",
        "no de espermatozoides por ml",
    ),
    _a(
        "semen_total", "Número total de espermatozoides", "", "10^6", "seminal", "no total de espermatozoides"
    ),
    _a("semen_prog_motility", "Motilidad progresiva", "", "%", "seminal", "motilidad progresiva"),
    _a("semen_nonprog_motility", "Motilidad no progresiva", "", "%", "seminal", "motilidad no progresiva"),
    _a("semen_total_motility", "Motilidad total", "", "%", "seminal", "motilidad total"),
    _a("semen_immotile", "Espermatozoides inmóviles", "", "%", "seminal", "espermatozoides inmoviles"),
    _a("semen_vitality", "Vitalidad espermática", "", "%", "seminal", "vitalidad"),
    _a("semen_head_abn", "Anormalidades de cabeza", "", "%", "seminal", "anormalidades de cabeza"),
    _a(
        "semen_midpiece_abn",
        "Anormalidades de pieza intermedia",
        "",
        "%",
        "seminal",
        "anormalidades de pieza intermedia",
    ),
    _a("semen_tail_abn", "Anormalidades de cola", "", "%", "seminal", "anormalidades de cola"),
    _a(
        "semen_cyto_excess",
        "Exceso de citoplasma residual",
        "",
        "%",
        "seminal",
        "exceso de citoplasma residual",
    ),
    _a(
        "semen_normal_morph",
        "Morfología normal",
        "",
        "%",
        "seminal",
        "espermatozoides con morfologia norma",
        "espermatozoides con morfologia normal",
    ),
    _a("semen_wbc", "Leucocitos en semen", "", "10^6/mL", "seminal"),
)


def _q(key, name, group, *aliases, kind="qual", unit=""):
    return Analyte(key, name, "", unit, group, tuple(aliases), kind)


# Resultados de texto (y tiras de orina, que pueden venir como texto o como cifra).
_QUALITATIVE: tuple[Analyte, ...] = (
    # Examen general de orina: físico y químico.
    _q("urine_color", "Color (orina)", "orina"),
    _q("urine_appearance", "Aspecto (orina)", "orina"),
    _q("urine_nitrite", "Nitritos (orina)", "orina", "nitritos"),
    _q(
        "urine_leuk_esterase",
        "Esterasa leucocitaria",
        "orina",
        "esterasa leucocitaria",
        "leucocitos esterasa",
    ),
    _q("urine_protein", "Proteínas (orina)", "orina", kind="mixed", unit="mg/dL"),
    _q("urine_glucose", "Glucosa (orina)", "orina", kind="mixed", unit="mg/dL"),
    _q(
        "urine_ketones",
        "Cetonas (orina)",
        "orina",
        "cetonas",
        "cuerpos cetonicos",
        kind="mixed",
        unit="mg/dL",
    ),
    _q("urine_bilirubin", "Bilirrubina (orina)", "orina", kind="mixed", unit="mg/dL"),
    _q("urine_urobilinogen", "Urobilinógeno (orina)", "orina", "urobilinogeno", kind="mixed", unit="mg/dL"),
    _q("urine_blood", "Sangre / hemoglobina (orina)", "orina"),
    # Examen general de orina: microscópico.
    _q("urine_wbc_micro", "Leucocitos por campo (orina)", "orina"),
    _q("urine_rbc_micro", "Eritrocitos por campo (orina)", "orina"),
    _q("urine_dysmorphic_rbc", "Eritrocitos dismórficos (orina)", "orina", "eritrocitos dismorficos"),
    _q("urine_casts", "Cilindros (orina)", "orina", "cilindros"),
    _q("urine_crystals", "Cristales (orina)", "orina", "cristales"),
    _q(
        "urine_squamous",
        "Células epiteliales escamosas",
        "orina",
        "celulas pavimentosas",
        "c epitelio escamoso",
    ),
    _q("urine_transitional", "Células de transición", "orina", "celulas de transicion"),
    _q("urine_renal_tubular", "Células tubulares renales", "orina", "celulas tubulares renales"),
    _q("urine_mucus", "Filamento mucoide (orina)", "orina", "redes mucoides", "filamento mucoide"),
    _q("urine_bacteria", "Bacterias (orina)", "orina"),
    _q("urine_yeast", "Levaduras (orina)", "orina"),
    # Espermatobioscopía.
    _q("semen_appearance", "Apariencia (semen)", "seminal", "apariencia"),
    _q("semen_color", "Color (semen)", "seminal"),
    _q("semen_liquefaction", "Licuefacción", "seminal", "licuefaccion"),
    _q("semen_fructose", "Fructosa (semen)", "seminal", "fructosa"),
    _q("semen_agglutination", "Aglutinación", "seminal", "aglutinacion"),
    _q("semen_immature_cells", "Células germinales inmaduras", "seminal", "celulas germinales inmaduras"),
    _q("semen_bacteria", "Bacterias (semen)", "seminal"),
    _q("semen_rbc", "Eritrocitos (semen)", "seminal"),
    # Serología y cultivos.
    _q("mycoplasma_hominis", "Cultivo de Mycoplasma hominis", "infecciosas", "cultivo de mycoplasma hominis"),
    _q("ureaplasma", "Cultivo de Ureaplasma urealyticum", "infecciosas", "cultivo de ureaplasma urealyticum"),
    _q(
        "hiv_ab",
        "Anticuerpos anti-VIH 1-2",
        "infecciosas",
        "anticuerpos anti hiv 1 2",
        "anticuerpos anti vih 1 2",
    ),
    _q("vdrl", "VDRL", "infecciosas", "v d r l", "vdrl"),
    _q("hcv_ab", "Anticuerpos anti-VHC", "infecciosas", "anticuerpos anti vhc", "anticuerpos anti hcv"),
)

# Nombres que dependen de la sección del estudio: "pH" o "Leucocitos" significan cosas
# distintas en la orina, en su parte microscópica o en el espermiograma.
_BY_SECTION = {
    "orina": {
        "color": "urine_color",
        "aspecto": "urine_appearance",
        "ph": "urine_ph",
        "proteinas": "urine_protein",
        "glucosa": "urine_glucose",
        "bilirrubina": "urine_bilirubin",
        "bilirrubinas": "urine_bilirubin",
        "hemoglobina": "urine_blood",
        "sangre": "urine_blood",
        "leucocitos": "urine_leuk_esterase",
        "bacterias": "urine_bacteria",
        "levaduras": "urine_yeast",
        "albumina": "albumin_urine",
    },
    "orina_micro": {
        "leucocitos": "urine_wbc_micro",
        "eritrocitos": "urine_rbc_micro",
    },
    "semen": {
        "volumen": "semen_volume",
        "ph": "semen_ph",
        "color": "semen_color",
        "bacterias": "semen_bacteria",
        "eritrocitos": "semen_rbc",
        "leucocitos": "semen_wbc",
    },
}


def section_context(section: str | None) -> list[str]:
    """Contextos de catálogo que aplican a un encabezado de sección, del más al menos específico."""
    s = _norm(section or "")
    if "orina" in s or "urinalisis" in s:
        return ["orina_micro", "orina"] if "microscop" in s else ["orina"]
    if "espermato" in s or "seminograma" in s or "seminal" in s or "espermograma" in s:
        return ["semen"]
    return []


# Otras formas de escribir análisis ya catalogados.
_MORE_ALIASES = {
    "bun": ("nitrogeno de urea en sangre bun", "nitrogeno de urea en sangre"),
    "vldl": ("vldl colesterol",),
    "ggt": ("gama glutamil transpeptidasa",),
    "globulin": ("globulinas",),
    "alp": ("f alcalina total",),
    "ldh": ("ldh",),
    "mcv": ("volumen corp medio",),
    "mch": ("hemoglobina corp media",),
    "mchc": ("conc media de hemoglobina corp",),
    "rdw": ("ancho de distrib de eritrocitos cv",),
    "urine_sg": ("densidad",),
}

CATALOG: tuple[Analyte, ...] = tuple(
    replace(a, aliases=a.aliases + _MORE_ALIASES.get(a.key, ()))
    for a in _BASE + _EXTRA + _CHOPO + _QUALITATIVE
)

# Mismo nombre impreso, distinto analito según la unidad (p. ej. albúmina en suero vs. mg/L,
# "Linfocitos" en % vs. en miles/µL, "Leucocitos" en sangre vs. en semen).
_UNIT_VARIANTS = {
    ("albumin", "mg/l"): "albumin_urine",
    ("wbc", "millones/ml"): "semen_wbc",
    **{
        (f"{cell}_pct", u): f"{cell}_abs"
        for cell in ("neut", "lymph", "mono", "eos", "baso")
        for u in ("miles/ul", "103/ul", "x103/ul")
    },
}


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


_INDEX = {_norm(a): an for an in CATALOG for a in (*an.aliases, an.name)}
BY_KEY = {a.key: a for a in CATALOG}


def match_analyte(printed_name: str, unit: str | None = None, section: str | None = None) -> Analyte | None:
    name = _norm(printed_name)
    for ctx in section_context(section):
        if key := _BY_SECTION[ctx].get(name):
            return BY_KEY[key]
    found = _INDEX.get(name)
    if found and unit:
        variant = _UNIT_VARIANTS.get((found.key, units.norm_unit(unit)))
        if variant:
            return BY_KEY[variant]
    return found
