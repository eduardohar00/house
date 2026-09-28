from datetime import date

from house.privacy import Anonymizer

DOC = open("bench/cases/example-synthetic/document.txt", encoding="utf-8").read()


def test_removes_direct_identifiers():
    res = Anonymizer(["Eduardo Ejemplo García", "Ramos"]).scrub(DOC, reference_date=date(2026, 9, 22))
    for leak in [
        "EDUARDO",
        "GARCÍA",
        "GAEE830412HDFRJD09",
        "A-20260922-0417",
        "55 1234 5678",
        "Calle Ejemplo",
        "12/04/1983",
        "Ramos",
    ]:
        assert leak not in res.text, leak
    assert "[EDAD: 43 años]" in res.text
    assert {"NOMBRE", "CURP", "FOLIO", "TELEFONO", "DIRECCION", "EDAD"} <= set(res.counts())


def test_keeps_clinical_content():
    res = Anonymizer(["Eduardo Ejemplo García"]).scrub(DOC, reference_date=date(2026, 9, 22))
    assert "Glucosa" in res.text and "92 mg/dL" in res.text
    assert "22/09/2026" in res.text  # la fecha del estudio es necesaria para las tendencias


def test_accent_insensitive_known_names():
    res = Anonymizer(["María López"]).scrub("Familiar: maria lopez acompañó", reference_date=None)
    assert "maria" not in res.text.lower() and "[NOMBRE]" in res.text


def test_dates_of_the_study_not_treated_as_birth():
    res = Anonymizer().scrub("Fecha de toma: 01/02/2026", reference_date=None)
    assert "01/02/2026" in res.text
