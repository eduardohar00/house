import pytest

from house.normalize import ranges, terminology, units


def test_unit_conversions():
    assert units.to_canonical("vitamin_d", 92, "nmol/L", "ng/mL") == (36.86, "ng/mL")
    assert units.to_canonical("glucose", 5.0, "mmol/L", "mg/dL")[0] == pytest.approx(90.08, abs=0.01)
    assert units.to_canonical("creatinine", 88.4, "µmol/L", "mg/dL")[0] == pytest.approx(1.0, abs=0.01)
    assert units.to_canonical("hba1c", 42, "mmol/mol", "%")[0] == pytest.approx(6.0, abs=0.01)
    assert units.to_canonical("ldl", 108, "mg/dl", "mg/dL") == (108, "mg/dL")


def test_unknown_unit_raises():
    with pytest.raises(units.UnknownUnit):
        units.to_canonical("glucose", 90, "furlongs", "mg/dL")
    with pytest.raises(units.UnknownUnit):
        units.to_canonical("glucose", 90, None, "mg/dL")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("70 - 99", (70, 99)),
        ("< 100", (None, 100)),
        ("menor a 150", (None, 150)),
        ("> 40", (40, None)),
        ("40 o más", (40, None)),
        ("hasta 41", (None, 41)),
        ("0.70 - 1.30", (0.7, 1.3)),
        ("4,0 a 5,6", (4.0, 5.6)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_parse_ref(text, expected):
    assert ranges.parse_ref(text) == expected


def test_classify_and_deviation():
    assert ranges.classify(112, None, 100) == "high"
    assert ranges.classify(29, 30, 400) == "low"
    assert ranges.classify(50, 40, None) == "ok"
    assert ranges.deviation(112, None, 100) == pytest.approx(0.12)
    assert ranges.deviation(50, 40, None) == 0.0


def test_terminology_matches_aliases():
    assert terminology.match_analyte("Colesterol LDL (calculado)").key == "ldl"
    assert terminology.match_analyte("TGP (ALT)").key == "alt"
    assert terminology.match_analyte("Vitamina D 25-OH").key == "vitamin_d"
    assert terminology.match_analyte("Potasio") is None
