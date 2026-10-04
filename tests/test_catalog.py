"""El catálogo debe reconocer los nombres y unidades que imprimen laboratorios reales.

Solo nombres y unidades de análisis (sin valores ni datos personales).
"""

import pytest

from house.normalize import terminology, units

PRINTED = """ALBUMINA | g/dL
ALBUMINA | mg/L
AMILASA EN SUERO | U/L
AMPLITUD DE LA DISTRIBUCION ERITROCITARIA | %
BASOFILOS % | %
BASOFILOS ABSOLUTOS | 10^3/µL
BILIRRUBINA DIRECTA | mg/dL
BILIRRUBINA INDIRECTA | mg/dL
BILIRRUBINA TOTAL | mg/dL
CO2 TOTAL | mmol/L
COLESTEROL DE ALTA DENSIDAD (HDL) | mg/dL
CONCENTRACION MEDIA DE HEMOGLOBINA | g/dL
CREATINFOSFOQUINASA (CPK) | U/L
DESHIDROGENASA LACTICA | U/L
EOSINOFILOS % | %
EOSINOFILOS ABSOLUTOS | 10^3/µL
ERITROCITOS | 10^6/µL
FOSFATASA ALCALINA TOTAL | U/L
FOSFORO | mg/dL
GAMAGLUTAMIL TRANSPEPTIDASA (GGT) | U/L
GLOBULINA | g/dL
HEMATOCRITO | %
HEMOGLOBINA CORPUSCULAR MEDIA | pg
HIERRO SERICO | µg/dL
LEUCOCITOS | 10^3/µL
LINFOCITOS % | %
LINFOCITOS ABSOLUTOS | 10^3/µL
LIPASA | U/L
LIPIDOS TOTALES | mg/dL
MAGNESIO | mg/dL
MONOCITOS % | %
MONOCITOS ABSOLUTOS | 10^3/µL
NEUTROFILOS EN BANDA % | %
NEUTROFILOS EN BANDA ABSOLUTOS | 10^3/µL
NEUTROFILOS SEGMENTADOS % | %
NEUTROFILOS SEGMENTADOS ABSOLUTOS | 10^3/µL
NITROGENO UREICO (BUN) | mg/dL
PLAQUETAS | 10^3/µL
POTASIO | mmol/L
SODIO | mmol/L
TRANSAMINASA GLUTAMICO OXALACETICA (AST) | U/L
TRANSAMINASA GLUTAMICO PIRUVICA (ALT) | U/L
UREA SERICA | mg/dL
no-HDL COLESTEROL | mg/dL
ACIDO URICO | mg/dL
CALCIO | mg/dL
CLORO | mmol/L
COLESTEROL DE BAJA DENSIDAD (LDL) | mg/dL
COLESTEROL DE MUY BAJA DENSIDAD (VLDL) | mg/dL
CREATININA | mg/dL
GRAVEDAD ESPECIFICA |
INDICE ATEROGENICO |
INDICE LDL / HDL |
PROTEINAS TOTALES | g/dL
RELACION A/G |
TRIGLICERIDOS | mg/dL
VOLUMEN CORPUSCULAR MEDIO | fl
VOLUMEN PLAQUETARIO MEDIO | fl
pH |
T3 TOTAL | ng/dL
T3 LIBRE | pg/mL
T3 CAPTACION | UCT
TIROXINA (T4) TOTAL | µg/dL
T4 LIBRE | ng/dL
INDICE DE TIROXINA LIBRE | µg/dL
YODO PROTEICO HORMONAL | µg/dL
HORMONA ESTIMULANTE DE TIROIDES (TSH) | µUI/mL
HGM | pg
CMHG | g/dL
ADE | %
Cuenta de Plaquetas | 10^3uL
Eritrocitos | 10^6uL
Hemoglobina Glucosilada (HB A1C) Fracción A1C | %
Lipoproteina VLDL | mg/dL
Proteína C Reactiva (Ultra sensible) | mg/L
T.G.O. (AST) | UI/L
T.G.P. (ALT) | UI/L
Fosfatasa Alcalina | UI/L
Albúmina Sérica | g/dL
Amilasa Sérica | UI/L
Capacidad de fijación de transferrina | ug/dL
Nitrógeno de Urea | mg/dL
Creatinina Sérica | mg/dL
Creatinina en Orina Aislada | mg/dL
Relación Albúmina / Creatinina en Orina Aislada | mg/g
Depuración de Creatinina Calculada | mL/min
Sodio Sérico | mmol/L
Dióxido de carbono (CO2) | mmol/L
Testosterona Total | nmol/L
Vitamina B - 12 | pg/mL
Mielocitos | %"""
CASES = [tuple(x.strip() for x in line.rsplit("|", 1)) for line in PRINTED.splitlines()]


@pytest.mark.parametrize("name,unit", CASES)
def test_real_names_and_units_are_recognized(name, unit):
    analyte = terminology.match_analyte(name, unit)
    assert analyte is not None, name
    value, canon = units.to_canonical(analyte.key, 1.0, unit, analyte.unit)
    assert canon == analyte.unit


def test_albumin_is_told_apart_by_unit():
    assert terminology.match_analyte("ALBUMINA", "g/dL").key == "albumin"
    assert terminology.match_analyte("ALBUMINA", "mg/L").key == "albumin_urine"


def test_keys_are_unique_and_aliases_do_not_collide():
    keys = [a.key for a in terminology.CATALOG]
    assert len(keys) == len(set(keys))
    seen: dict[str, str] = {}
    for a in terminology.CATALOG:
        for alias in (*a.aliases, a.name):
            n = terminology._norm(alias)
            assert seen.setdefault(n, a.key) == a.key, f"alias repetido entre analitos: {alias}"


def test_every_analyte_has_a_plain_explanation_and_none_is_orphaned():
    from house.normalize import explanations, terminology

    assert explanations.missing() == []
    assert set(explanations.ABOUT) <= {a.key for a in terminology.CATALOG}
    assert all(3 <= len(t) <= 260 and t.endswith(".") for t in explanations.ABOUT.values())


def test_catalog_endpoint_carries_the_explanation(tmp_path):
    from fastapi.testclient import TestClient

    from house.app.api import create_app

    c = TestClient(create_app(tmp_path, key_provider=lambda: b"k" * 32))
    cat = c.get("/api/catalog").json()
    assert "azúcar" in cat["glucose"]["about"] and cat["gi_norovirus"]["about"]


def test_blood_count_absolute_cells_with_103ul_unit():
    assert terminology.match_analyte("Neutrófilos", "10^3uL").key == "neut_abs"
    assert terminology.match_analyte("Neutrófilos", "%").key == "neut_pct"


def test_urine_sediment_names_and_cells_per_field():
    sed = "Examen General de Orina > Sedimento"
    assert terminology.match_analyte("Eritrocitos", "cel/HPF", sed).key == "urine_rbc_micro"
    assert terminology.match_analyte("Cilindros hialinos", None, sed).key == "urine_casts_hyaline"
    assert terminology.match_analyte("Células Uroteliales", None, sed).key == "urine_transitional"
    assert terminology.match_analyte("Leucocitos (Esterasa leucocitaria)", "cel/uL", sed).key == (
        "urine_leuk_esterase_count"
    )
