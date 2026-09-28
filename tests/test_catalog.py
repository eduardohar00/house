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
no-HDL COLESTEROL | mg/dL"""
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
