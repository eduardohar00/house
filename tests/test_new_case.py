import json
from pathlib import Path

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
