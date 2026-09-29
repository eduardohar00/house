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


def test_collected_on_from_short_date_but_not_birth_date():
    from house.bench.new_case import prefill_expected

    text = "Fecha de nacimiento:01/02/1990\nFecha: 05/06/19 08:54 Edad:29 años\nGlucosa 90 55 - 99 mg/dL"
    assert prefill_expected(text)[0]["collected_on"] == "2019-06-05"


# Secciones y resultados de texto, con valores inventados.
SECTIONS_DOC = """ESPERMATOBIOSCOPÍA (SEMINOGRAMA)
Volumen 3.0 > = 1.5 mL
pH 7.8 > = 7.2
Licuefacción Incompleta Completa
Leucocitos 0 0 - 1 millones/mL
BIOMETRÍA HEMÁTICA COMPLETA
Leucocitos 6.00 3.8-11.6 miles/µL
AC A VIRUS DE HEPATITIS C EN SUERO
Anticuerpos anti-VHC No Reactivo No reactivo
Cualquier resultado positivo debe confirmarse con un método treponemico como FTA.
EXAMEN GENERAL DE ORINA
EXAMEN FÍSICO ___
Color Ámbar Amarillo
pH 6.0 4.8 - 7.4
EXAMEN QUÍMICO ___
Leucocitos Negativo Negativo ó < 10
Urobilinógeno 1 Negativo ó < 1
mg/dL
SUCURSAL CENTRO
MIGUEL HIDALGO, CMX
Glucosa Negativo Negativo mg/dL
EXAMEN MICROSCÓPICO ___
Leucocitos Ausentes Ausentes ó 1 - 5
ERITROCITOS AUSENTES / Campo
CILINDROS NEGATIVO / Campo NEGATIVO
Cristales Urato Amorfo Ausentes
Células Pavimentosas Escasas Ausentes - Escasas
Estadio G1: >90 mL/min/1.73m2 TFG normal
valores menores a 60 mL/min/1.73 m2, es decir, las clasificaciones G3a – G5, por"""


def test_sections_and_text_results():
    from house.extract import process
    from house.providers import LLMRequest
    from house.providers.mock import BaselineRegexProvider
    from house.schema import RawExtraction

    req = LLMRequest(task="extract", system="", user=SECTIONS_DOC, schema={})
    raw = RawExtraction.model_validate(BaselineRegexProvider().complete_json(req).data)
    rows = process(raw, SECTIONS_DOC)
    got = {(r.key, r.value if r.value is not None else r.value_label, r.status) for r in rows}
    assert got == {
        ("semen_volume", 3.0, "ok"),
        ("semen_ph", 7.8, "ok"),
        ("semen_liquefaction", "Incompleta", "abnormal"),
        ("semen_wbc", 0.0, "ok"),
        ("wbc", 6.0, "ok"),
        ("hcv_ab", "No Reactivo", "ok"),
        ("urine_color", "Ámbar", "abnormal"),
        ("urine_ph", 6.0, "ok"),
        ("urine_leuk_esterase", "Negativo", "ok"),
        ("urine_urobilinogen", 1.0, "high"),
        ("urine_glucose", "Negativo", "ok"),
        ("urine_wbc_micro", "Ausentes", "ok"),
        ("urine_rbc_micro", "AUSENTES", None),
        ("urine_casts", "NEGATIVO", "ok"),
        ("urine_crystals", "Urato Amorfo", "abnormal"),
        ("urine_squamous", "Escasas", "ok"),
    }


def test_method_applies_to_the_rows_before_it():
    from house.providers import LLMRequest
    from house.providers.mock import BaselineRegexProvider

    doc = """Glucosa 90 55 - 99 mg/dL
Urea 30 14 - 50 mg/dL
Método:Fotometría automatizada
Antígeno Carcinoembrionario 1.00 < 5.2 ng/mL
Este resultado se obtuvo utilizando como metodología electroquimioluminiscencia.
Método: Electroquimioluminiscencia
Hematócrito 45.0 40.0-54.0 %"""
    rows = (
        BaselineRegexProvider()
        .complete_json(LLMRequest(task="extract", system="", user=doc, schema={}))
        .data["rows"]
    )
    assert [r["method"] for r in rows] == [
        "Fotometría automatizada",
        "Fotometría automatizada",
        "Electroquimioluminiscencia",
        None,
    ]


def test_text_result_with_unit_after_reference_is_not_read_as_number():
    from house.bench.new_case import prefill_expected

    doc = """EXAMEN GENERAL DE ORINA
EXAMEN QUÍMICO ___
Leucocitos Negativo Negativo ó < 10 leu/uL
Hemoglobina Negativo Negativo ó < 5 eri/uL
REACCIONES FEBRILES EN SUERO
Tífico O Negativo Negativo
Proteus OX-19 Negativo Negativo"""
    exp, unrecognized = prefill_expected(doc)
    assert {(r["key"], r["value"]) for r in exp["results"]} == {
        ("urine_leuk_esterase", "Negativo"),
        ("urine_blood", "Negativo"),
        ("typhoid_o", "Negativo"),
        ("proteus_ox19", "Negativo"),
    }
    assert unrecognized == []


def test_homa_block_repeats_and_urate_crystals():
    from house.extract import Provenance, process
    from house.providers import LLMRequest
    from house.providers.mock import BaselineRegexProvider
    from house.schema import RawExtraction

    doc = """QUÍMICA INTEGRAL DE 45 ELEMENTOS
Glucosa 90 55 - 99 mg/dL
sd LDL 2.00 0 - 1.35
HOMA-IR
Glucosa 91 55 - 99 mg/dL
Glucosa 5.05 < 5.55 mmol/L
Insulina basal 10.00 2.6 - 24.9 µU/mL
HOMA-IR 2.30 < 2.7
Vitamina D (25,hidroxi) 35.0 30 - 100 ng/mL
EXAMEN GENERAL DE ORINA
EXAMEN MICROSCÓPICO ___
Cristales . Ausentes
Urato Amorfo Presentes Ausentes"""
    raw = RawExtraction.model_validate(
        BaselineRegexProvider().complete_json(LLMRequest(task="extract", system="", user=doc, schema={})).data
    )
    rows = process(raw, doc)
    glucose = [r for r in rows if r.key == "glucose"]
    assert [r.value for r in glucose] == [90, 91, 90.98]
    assert Provenance.SAME_IN_OTHER_UNIT in glucose[2].problems
    assert all(Provenance.REPEATED in r.problems for r in glucose[:2])
    got = {
        r.key: (r.value if r.value is not None else r.value_label, r.status)
        for r in rows
        if r.key != "glucose"
    }
    assert got == {
        "sd_ldl": (2.0, "high"),
        "insulin": (10.0, "ok"),
        "homa_ir": (2.3, "ok"),
        "vitamin_d": (35.0, "ok"),
        "urine_urate_crystals": ("Presentes", "abnormal"),
    }


def test_hba1c_name_and_interpretation_legend():
    from house.bench.new_case import prefill_expected

    doc = """HEMOGLOBINA GLICOSILADA A1c
Hemoglobina glicosilada A1c 5.0 4.0 - 5.7 %
Normal: 4.0% a 5.7%
Prediabetes: 5.7% a 6.4%"""
    exp, unrecognized = prefill_expected(doc)
    assert [(r["key"], r["value"]) for r in exp["results"]] == [("hba1c", 5.0)]
    assert unrecognized == []
