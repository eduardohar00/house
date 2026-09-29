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


def test_never_joins_lines_and_covers_lab_header_variants():
    doc = (
        "Paciente : PEREZ FICTICIO JUAN\n"
        "SOLICITUD : 0000000001\n"
        "Fec. Nac. : 01/02/1980 Edad : 46A\n"
        "Médico : Dr. ALGUIEN INVENTADO SOTO\n"
        "Paciente:PEREZ FICTICIO JUAN Sexo: Masculino\n"
        "Dirigido a:DR(A). OTRO NOMBRE FALSO Hoja: 1 de 8\n"
        "Fecha de nacimiento:01/02/1980 Edad:46 años\n"
        "Glucosa 90 55 - 99 mg/dL\n"
        "interpretación hecha por el médico tratante."
    )
    res = Anonymizer().scrub(doc, reference_date=date(2026, 3, 1))
    assert len(res.text.splitlines()) == len(doc.splitlines())
    for leak in ["PEREZ", "FICTICIO", "01/02/1980", "ALGUIEN", "INVENTADO", "SOTO", "OTRO NOMBRE", "FALSO"]:
        assert leak not in res.text, leak
    for keep in [
        "SOLICITUD",
        "Sexo: Masculino",
        "Hoja: 1 de 8",
        "Glucosa 90 55 - 99 mg/dL",
        "médico tratante",
    ]:
        assert keep in res.text, keep
    assert res.text.count("[EDAD: 46 años]") == 2


def test_administrative_ids_anywhere_in_line():
    doc = (
        "12345678 Orden:AB0012345\n"
        "87654321\n"
        "CD0098765\n"
        "SOLICITUD : 0000000001\n"
        "Episodio : 9999999999 Habitación/Cama: XYZ123-Cama123\n"
        "N° Paciente : 5555555\n"
        "Glucosa 90 55 - 99 mg/dL\n"
        "5 0 6"
    )
    res = Anonymizer().scrub(doc)
    for leak in [
        "12345678",
        "AB0012345",
        "87654321",
        "CD0098765",
        "0000000001",
        "9999999999",
        "XYZ123",
        "5555555",
    ]:
        assert leak not in res.text, leak
    assert "Glucosa 90 55 - 99 mg/dL" in res.text and "5 0 6" in res.text
    assert len(res.text.splitlines()) == len(doc.splitlines())
