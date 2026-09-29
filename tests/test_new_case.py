import json
from pathlib import Path

import pytest

from house.bench.new_case import detect_identifiers, scaffold
from house.bench.runner import run

DOC = Path("bench/cases/example-synthetic/document.txt").read_text(encoding="utf-8")


def test_scaffold_prefills_and_marks_unverified(tmp_path):
    info = scaffold(tmp_path / "caso", DOC, ["Eduardo Ejemplo García"])
    exp = json.loads((tmp_path / "caso/expected.json").read_text(encoding="utf-8"))
    prof = json.loads((tmp_path / "caso/profile.json").read_text(encoding="utf-8"))
    assert exp["verified"] is False and info["prefilled"] == 9
    assert exp["collected_on"] == "2026-09-22"
    assert "Eduardo Ejemplo García" in prof["forbidden"]
    assert any("GAEE830412" in f for f in prof["forbidden"])
    assert any("A-20260922-0417" in f for f in prof["forbidden"])


def test_unverified_cases_are_skipped_by_the_benchmark(tmp_path, capsys):
    scaffold(tmp_path / "caso", DOC, [])
    assert run(tmp_path, None, None) == []
    assert "no está verificado" in capsys.readouterr().err
    exp_path = tmp_path / "caso/expected.json"
    exp = json.loads(exp_path.read_text(encoding="utf-8"))
    exp["verified"] = True
    exp_path.write_text(json.dumps(exp), encoding="utf-8")
    assert len(run(tmp_path, None, None)) == 1


def test_detect_identifiers():
    ids = detect_identifiers(DOC)
    assert "GAEE830412HDFRJD09" in ids


def test_scaffold_from_collapsed_spacing(tmp_path):
    import re

    squashed = re.sub(r"[ \t]+", " ", DOC)
    assert scaffold(tmp_path / "c", squashed, [])["prefilled"] == 9


def test_names_command_prints_no_values():
    from house.bench.names import names_and_units

    pairs = names_and_units(["Potasio 4.1 mmol/L 3.5 - 5.1", "Leucocitos 6.2 10^3/uL 4 - 11"])
    assert ("Potasio", "mmol/L") in pairs
    assert all(not any(ch.isdigit() for ch in name) for name, _ in pairs)


def test_baseline_reads_hematology_units():
    from house.bench.names import names_and_units

    lines = [
        "Leucocitos 6.2 10^3/uL 4.0 - 11.0",
        "Eritrocitos 5.1 x10^6/µL 4.5 - 5.9",
        "Volumen corpuscular medio 88 fL 80 - 100",
        "Hemoglobina corpuscular media 29 pg 27 - 33",
        "Hematocrito 45 % 40 - 52",
        "Velocidad de sedimentación 5 mm/h 0 - 15",
    ]
    got = {n for n, _ in names_and_units(lines)}
    assert got == {
        "Leucocitos",
        "Eritrocitos",
        "Volumen corpuscular medio",
        "Hemoglobina corpuscular media",
        "Hematocrito",
        "Velocidad de sedimentación",
    }


# Formato de un laboratorio real con valores inventados.
LAB_LINES = """CREATININA 1.50 * mg/dL 0.60 - 1.20
VOLUMEN CORPUSCULAR MEDIO 85.0 fl 81.0 - 99.0
ALBUMINA 4.00 g/dL 3.10 - 4.50
ALBUMINA 12.0 mg/L 0.0 - 23.8
RELACION A/G 1.20 1.00 - 2.20
INDICE LDL / HDL 3.1 * <2.7
INDICE ATEROGENICO 5.0 * mg/dL <3.5
pH 6.0 5.0 - 7.0
T3 CAPTACION 1.10 UCT 0.69 - 1.41
LIMÍTROFE 150 - 199
ALTO = ó >200
CÉDULA PROFESIONAL : 1234567
Fecha de Toma : 01/03/2026"""


def test_prefill_keeps_flagged_unitless_and_urine_rows():
    from house.bench.new_case import prefill_expected

    exp, unrecognized = prefill_expected(LAB_LINES)
    got = {(r["key"], r["value"], r["unit"]) for r in exp["results"]}
    assert got == {
        ("creatinine", 1.5, "mg/dL"),
        ("mcv", 85.0, "fL"),
        ("albumin", 4.0, "g/dL"),
        ("albumin_urine", 12.0, "mg/L"),
        ("ag_ratio", 1.2, ""),
        ("ldl_hdl_ratio", 3.1, ""),
        ("chol_hdl_ratio", 5.0, ""),
        ("urine_ph", 6.0, ""),
        ("t3_uptake", 1.1, ""),
    }
    assert unrecognized == []


def test_baseline_ref_text_for_unitless_rows():
    from house.providers import LLMRequest
    from house.providers.mock import BaselineRegexProvider

    req = LLMRequest(task="extract", system="", user="INDICE LDL / HDL 3.1 * <2.7", schema={})
    (row,) = BaselineRegexProvider().complete_json(req).data["rows"]
    assert row["unit_text"] is None and row["ref_text"] == "<2.7"


# Formato Chopo (valor, referencia, unidad al final) con valores inventados.
CHOPO_LINES = """Fecha de Registro:02/01/2024 08:00
Glucosa 90 55 - 99 mg/dL
Sodio 139 136 - 145 meq/L
Relación A/G 1.50 1.18 – 2.33
Eritrocitos 5.00 4.70-5.80
millones/µL
Conc. Media de Hemoglobina Corp. 33.0 32.0-36.0 g/dL (%)
Linfocitos 30.0 16.5-49.6 %
Linfocitos 1.50 1.05-3.53 miles/µL
No. de espermatozoides por mL 40 > = 15 millones/mL
Leucocitos 0 0 - 1 millones/mL
Estadio G2: 60-89 mL/min/1.73m2 TFG levemente disminuida
Dirigido a:DR(A). NOMBRE FICTICIO Hoja: 1 de 8
130 - 159 Cercano al óptimo"""


def test_prefill_reads_chopo_layout():
    from house.bench.new_case import prefill_expected

    exp, unrecognized = prefill_expected(CHOPO_LINES)
    got = {(r["key"], r["value"], r["unit"]) for r in exp["results"]}
    assert got == {
        ("glucose", 90.0, "mg/dL"),
        ("sodium", 139.0, "mmol/L"),
        ("ag_ratio", 1.5, ""),
        ("rbc", 5.0, "10^6/µL"),
        ("mchc", 33.0, "g/dL"),
        ("lymph_pct", 30.0, "%"),
        ("lymph_abs", 1.5, "10^3/µL"),
        ("semen_conc", 40.0, "10^6/mL"),
        ("semen_wbc", 0.0, "10^6/mL"),
    }
    assert unrecognized == []
    assert exp["collected_on"] == "2024-01-02"


def test_percent_analyte_without_unit_is_not_assumed_percent():
    from house.normalize import units

    with pytest.raises(units.UnknownUnit):
        units.to_canonical("lymph_pct", 1.5, None, "%")
