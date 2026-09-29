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


def test_strict_limits():
    assert ranges.classify_ref(1, ranges.parse_ref_full("Negativo ó < 1")) == "high"
    assert ranges.classify_ref(1, ranges.parse_ref_full("<= 1")) == "ok"
    assert ranges.classify_ref(1, ranges.parse_ref_full("< = 1")) == "ok"
    assert ranges.classify_ref(1, ranges.parse_ref_full("hasta 1")) == "ok"
    assert ranges.classify_ref(60, ranges.parse_ref_full("> 60")) == "low"
    assert ranges.classify_ref(15, ranges.parse_ref_full("> = 15")) == "ok"
    assert ranges.classify_ref(99, ranges.parse_ref_full("70 - 99")) == "ok"


def test_text_results():
    assert ranges.classify_text("NEGATIVO", "Leu/µL NEGATIVO") == "ok"
    assert ranges.classify_text("Positiva", "Positiva") == "ok"
    assert ranges.classify_text("Ausente", "Ausentes ó 1 - 5") == "ok"
    assert ranges.classify_text("Incompleta", "Completa") == "abnormal"
    assert ranges.classify_text("Negativo", None) is None
    assert ranges.classify_text("AUSENTES", "/ Campo") is None
    assert ranges.classify_text("NEGATIVO", "/ Campo NEGATIVO") == "ok"


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
    assert terminology.match_analyte("Tiroglobulina") is None


def test_mixed_methods():
    from house.normalize.series import mixed_methods

    assert mixed_methods(["Electroquimioluminiscencia", "electroquimioluminiscencia", None]) == []
    assert mixed_methods(["Electroquimioluminiscencia", "Quimioluminiscencia"]) == [
        "Electroquimioluminiscencia",
        "Quimioluminiscencia",
    ]


def test_pointer_references_and_expected_negative_tests():
    from house.normalize import ranges, terminology

    # «Ver Anexo» no dice cuál es el resultado esperado: sin referencia, no "fuera de rango"
    assert ranges.classify_text("NO DETECTADO", "Ver Anexo") is None
    assert (
        ranges.classify_text("Negativo", "Negativo") == "ok"
        and ranges.classify_text("Positivo", "Negativo") == "abnormal"
    )
    # pruebas de presencia de un microorganismo: lo esperado es no detectarlo
    assert ranges.classify_text("NO DETECTADO", "Ver Anexo", expect_negative=True) == "ok"
    assert ranges.classify_text("DETECTADO", "Ver Anexo", expect_negative=True) == "abnormal"
    assert ranges.classify_text("No Reactivo", None, expect_negative=True) == "ok"
    assert ranges.classify_text("Indeterminado", None, expect_negative=True) is None
    assert ranges.classify_text("Ausentes", None) is None  # sin la marca, sin referencia sigue sin estado
    assert terminology.expects_negative("gi_norovirus") and terminology.expects_negative("giardia_antigen")
    assert not terminology.expects_negative("urine_color") and not terminology.expects_negative("glucose")


def test_repair_fixes_stored_text_statuses(tmp_path):
    import sqlite3

    from house.app import ingest

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE observation(id INTEGER PRIMARY KEY, analyte_key, value_num, value_text, unit, unit_printed, "
        "ref_printed, ref_low, ref_high, status, qualifier)"
    )
    db.execute(
        "INSERT INTO observation(analyte_key, value_text, ref_printed, status) "
        "VALUES('gi_norovirus', 'NO DETECTADO', 'Ver Anexo', 'abnormal'), ('urine_nitrite', 'Positivo', 'Negativo', 'abnormal')"
    )
    assert ingest.repair_references(db) == 1
    assert [r["status"] for r in db.execute("SELECT status FROM observation ORDER BY id")] == [
        "ok",
        "abnormal",
    ]
    assert ingest.repair_references(db) == 0  # idempotente
