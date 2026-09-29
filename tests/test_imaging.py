"""Informes de imagen: lectura de secciones, revisión, migración y borrado (datos inventados)."""

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from house.app import ingest
from house.app.api import create_app
from house.config import Config, ProviderConfig
from house.imaging import looks_like_imaging_report, normal_flag, parse_reports, parse_spanish_date
from house.providers import Router
from test_app import ADMIN, KEY, H, make_pdf, upload

REPORT = """Fecha: domingo, 4 de mayo de 2025 Reservación: 1000000001
Paciente: PEREZ FICTICIO, JUAN CARLOS Sexo: Masculino
Fecha de nacimiento: 11/Jul./1992 Edad: 32 años
Sucursal: CENTRO
Nombre del estudio: TORAX OSEO AP Y LATERAL
T écnica.
Radiografía anteroposterior y lateral de tórax óseo.
Indicación del estudio.
Revisión general.
Hallazgos.
Estructuras óseas con radiopacidad normal.
Arcos costales en número normal.
Estudio previo.
Sin previos.
Conclusión.
Estudio con características normales.
Sugerencias.
Correlación clínica.
ATENTAMENTE
DR. ALGUIEN INVENTADO SOTO
Médico Especialista en Radiología e Imagen. Ced. 1
Página 1 de 1
La interpretación del resultado debe realizarse siempre por su médico.
Fecha: lunes, 5 de mayo de 2025 Reservación: 1000000002
Paciente: PEREZ FICTICIO, JUAN CARLOS Sexo: Masculino
Nombre del estudio: RODILLA DERECHA AP Y LATERAL
Técnica.
Radiografía anteroposterior y lateral de rodilla derecha.
Hallazgos.
Se observa disminución del espacio articular medial con osteofitos marginales.
Conclusión.
Cambios degenerativos incipientes de rodilla derecha.
Sugerencias.
Valoración por ortopedia.
ATENTAMENTE
DRA. OTRA INVENTADA RUIZ
Página 1 de 1"""


def test_parses_two_reports_with_sections_dates_and_flags():
    assert looks_like_imaging_report(REPORT)
    a, b = parse_reports(REPORT)
    assert (a.study_name, a.performed_on, a.modality) == (
        "Torax Oseo AP y Lateral",
        "2025-05-04",
        "Radiografía",
    )
    assert a.indication == "Revisión general." and a.prior == "Sin previos." and a.site == "CENTRO"
    assert a.findings == "Estructuras óseas con radiopacidad normal. Arcos costales en número normal."
    assert a.conclusion == "Estudio con características normales." and a.flag == "normal"
    assert a.radiologist == "Alguien Inventado Soto" and "Página" not in a.suggestions
    assert b.performed_on == "2025-05-05" and b.flag == "revisar"  # cambios degenerativos: la persona lo lee
    assert b.radiologist == "Otra Inventada Ruiz" and b.suggestions == "Valoración por ortopedia."


def test_lab_report_is_not_an_imaging_report_and_flags_are_conservative():
    assert not looks_like_imaging_report("Glucosa 90 55 - 99 mg/dL\nColesterol 180 < 200 mg/dL")
    assert normal_flag("Estudio con características normales.") == "normal"
    assert normal_flag("Sin alteraciones. Se sugiere control en 6 meses.") == "revisar"
    assert normal_flag("") == "revisar"
    assert parse_spanish_date("domingo, 4 de mayo de 2025") == "2025-05-04"
    assert parse_spanish_date("31 de febrero de 2025") is None


@pytest.fixture
def client(tmp_path):
    cfg = Config(tasks={"extract": "base"}, providers={"base": ProviderConfig(name="base", kind="mock")})
    return TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg)))


def test_upload_review_and_list_imaging(client):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    r = upload(c, me, make_pdf(REPORT.splitlines()), "Rx torax.pdf")
    assert r.status_code == 200 and r.json()["rows"] == 2
    doc_id = r.json()["document_id"]

    rev = c.get(f"/api/documents/{doc_id}").json()
    assert rev["document"]["doc_type"] == "imagen" and rev["rows"] == [] and len(rev["imaging"]) == 2
    assert rev["ai_saw"] == ""  # nada se envió a una IA
    assert [i["position"] for i in rev["imaging"]] == [0, 1]

    decisions = [
        {"position": 0, "accept": True},
        {"position": 1, "accept": True, "flag": "normal", "study_name": "Rodilla derecha AP y lateral"},
    ]
    saved = c.post(f"/api/documents/{doc_id}/review-imaging", headers=H, json={"decisions": decisions})
    assert saved.json() == {"saved": 2}
    listing = c.get(f"/api/people/{me}/imaging").json()
    assert [(x["study_name"], x["flag"], x["performed_on"]) for x in listing] == [
        ("Rodilla derecha AP y lateral", "normal", "2025-05-05"),
        ("Torax Oseo AP y Lateral", "normal", "2025-05-04"),
    ]
    docs = c.get(f"/api/people/{me}/documents").json()
    assert (
        docs[0]["doc_type"] == "imagen" and docs[0]["results"] == 2 and docs[0]["review_state"] == "revisada"
    )
    assert (
        c.post(f"/api/documents/{doc_id}/review-imaging", headers=H, json={"decisions": []}).status_code
        == 409
    )

    c.delete(f"/api/documents/{doc_id}", headers=H).raise_for_status()
    assert c.get(f"/api/people/{me}/imaging").json() == []  # sin informes huérfanos


def test_reread_and_permissions(client):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    admin_id = c.get("/api/me").json()["id"]
    member = {"display_name": "Miembro", "birth_date": "1992-02-02", "sex_at_birth": "F", "pin": "2468"}
    mid = c.post("/api/people", json=member, headers=H).json()["id"]
    doc_id = upload(c, admin_id, make_pdf(REPORT.splitlines())).json()["document_id"]
    assert c.post(f"/api/documents/{doc_id}/reread", headers=H).json()["rows"] == 2
    c.post("/api/logout", headers=H)
    c.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H).raise_for_status()
    assert c.get(f"/api/people/{admin_id}/imaging").status_code == 403


def test_migration_adds_columns_to_an_existing_database(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE person(id INTEGER PRIMARY KEY); CREATE TABLE document(id INTEGER PRIMARY KEY);"
        "CREATE TABLE imaging_study(id INTEGER PRIMARY KEY, person_id INTEGER, document_id INTEGER,"
        " modality TEXT NOT NULL, region TEXT, performed_on TEXT NOT NULL, report_text TEXT, dicom_dir TEXT);"
        "INSERT INTO imaging_study(modality, performed_on) VALUES ('Radiografía', '2020-01-01');"
    )
    ingest.migrate_imaging(db)
    ingest.migrate_imaging(db)  # idempotente
    cols = {r["name"] for r in db.execute("PRAGMA table_info(imaging_study)")}
    assert {"study_name", "conclusion", "flag", "confirmed_by"} <= cols
    assert db.execute("SELECT COUNT(*) FROM imaging_study").fetchone()[0] == 1  # nada se pierde


HOSPITAL = """Departamento de Imagenologia
INFORME RADIOLÓGICO
Nombre Paciente: PEREZ FICTICIO JUAN Sexo: H Edad: 33Y
ID Paciente: 123 Fec.Nac: 01/01/1990
Médico: Inventado Uno
Código de Estudio Descripción Realizado
IMA-720006 RADIOGRAFIA DE ABDOMEN (2 PROYECCIONES) 06/02/2026 11:06 a. m.
Order ID: 999
Radiografía de abdomen de pie y decúbito
Estructuras óseas conservadas.
No se observan visceromegalias.
Nom. Paciente:PEREZ FICTICIO JUAN Sexo: H
Impreso: 06/02/2026
Conclusión.
Sin hallazgos relevantes.
Aprobado por:Dra. Persona Inventada Ruiz
CP: 1 C.ESP: 2"""

LEGACY = """56392629 Orden: OK350125
Id Paciente: 396218
ULTRASONIDO
Paciente: PEREZ FICTICIO JUAN
Edad: 31 años Sexo: Masculino
Fecha: 27/12/2023 09:36:50 a. m.
Hoja 1 de 1
Dirigido a: ALGUIEN INVENTADO
ULTRASONIDO TESTICULAR
Técnica: Se realiza estudio con transductor lineal, reportando los siguientes hallazgos:
Testículo derecho de morfología conservada.
Testículo izquierdo de morfología conservada.
Conclusión:
- Sin patología demostrable, normal.
Atentamente,
Dr. Firmante Inventado Soto"""

ENDOSCOPY_OCR = """INFORME DEL ESTUDIO
HOSPITAL INVENTADO
Paciente:
PEREZ FICTICIO JUAN
Procedimiento:
PANENDOSCOPIA
Fecha del Estudio: 07/Ago/2014 09:52 AM
HALLAZGOS
diagnostico preendoscopico.
enfermedad acido-peptica
diagnostico pósendoscopico.
esofagitis b de los angeles
gastropatia erosiva"""


def test_hospital_format_technique_findings_conclusion_and_radiologist():
    (r,) = parse_reports(HOSPITAL, "2026-02-06 - Rx de Abdomen.pdf")
    assert (r.modality, r.performed_on) == ("Radiografía", "2026-02-06")
    assert r.study_name == "Radiografia de abdomen (2 proyecciones)"
    assert r.technique == "Radiografía de abdomen de pie y decúbito"
    assert "visceromegalias" in r.findings and "Nom. Paciente" not in r.findings and "PEREZ" not in r.findings
    assert r.conclusion == "Sin hallazgos relevantes." and r.flag == "revisar"
    assert r.radiologist == "Persona Inventada Ruiz"


def test_legacy_format_technique_ends_where_findings_start():
    (r,) = parse_reports(LEGACY, "27-12-2023 Ultrasonido testicular.pdf")
    assert (r.study_name, r.modality, r.performed_on) == (
        "Ultrasonido testicular",
        "Ultrasonido",
        "2023-12-27",
    )
    assert r.technique.startswith("Se realiza estudio") and "Testículo derecho" in r.findings
    assert r.technique.count("Testículo") == 0  # la técnica no se traga los hallazgos
    assert r.conclusion == "- Sin patología demostrable, normal." and r.flag == "normal"
    assert r.radiologist == "Firmante Inventado Soto"


def test_generic_report_keeps_text_uses_filename_date_and_drops_the_patient_name():
    (r,) = parse_reports(ENDOSCOPY_OCR, "07-08-2014 Panendoscopia.pdf")
    assert (r.modality, r.performed_on, r.study_name) == ("Endoscopia", "2014-08-07", "Panendoscopia")
    assert "esofagitis b de los angeles" in r.conclusion and "PEREZ" not in (r.findings + r.conclusion)
    assert r.flag == "revisar"  # sin estructura conocida nunca se marca "normal"
    (n,) = parse_reports(
        "Plan de alimentación semanal: desayuno avena con fruta, comida pollo con verduras",
        "14-09-2025 - Nutriologa.pdf",
    )
    assert (n.modality, n.performed_on) == ("Nutrición", "2025-09-14")
    assert parse_reports("x", "vacio.pdf") == []


def test_pathology_is_not_labelled_endoscopy_and_dates_with_month_names():
    (r,) = parse_reports(
        "DIAGNÓSTICO\n1.- Biopsias de esófago: esofagitis crónica leve. Sin displasia.",
        "Resultados Biopsias Panendoscopia.pdf",
    )
    assert r.modality == "Patología"
    assert parse_spanish_date("07/Ago/2014 09:52 AM") == "2014-08-07"
    assert parse_spanish_date("jueves, 14 de agosto de 2014") == "2014-08-14"
    assert parse_spanish_date("27-Julio-2024") == "2024-07-27"


def test_scanned_pdf_is_read_with_ocr_and_becomes_a_report(client, monkeypatch):
    from house.app import ocr

    monkeypatch.setattr(ocr, "ocr_bytes", lambda data, suffix, work_dir: ENDOSCOPY_OCR)
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    r = upload(c, me, make_pdf([]), "07-08-2014 Panendoscopia.pdf")  # PDF sin texto: un escaneo
    assert r.status_code == 200 and r.json()["kind"] == "imagen"
    rev = c.get(f"/api/documents/{r.json()['document_id']}").json()
    assert rev["imaging"][0]["modality"] == "Endoscopia" and rev["document"]["collected_on"] == "2014-08-07"

    def unavailable(data, suffix, work_dir):
        raise ocr.OcrUnavailable("Instala las herramientas de desarrollo de Apple.")

    monkeypatch.setattr(ocr, "ocr_bytes", unavailable)
    r = upload(c, me, make_pdf([]) + b"\n%otro", "escaneo2.pdf")
    assert r.status_code == 422 and "herramientas" in r.json()["detail"]["message"]


@pytest.mark.skipif(not shutil.which("swiftc"), reason="requiere las herramientas de desarrollo de Apple")
def test_real_ocr_reads_text_from_an_image_only_pdf(tmp_path, monkeypatch):
    """Integración: compila el lector y lee un PDF que solo trae una imagen (una vez, ~10 s)."""
    import io

    from PIL import Image, ImageDraw, ImageFont

    from house.app import ocr

    monkeypatch.delenv("HOUSE_OCR", raising=False)
    img = Image.new("RGB", (1200, 400), "white")
    ImageDraw.Draw(img).text(
        (40, 150), "GLUCOSA EN AYUNAS 92 MG/DL", fill="black", font=ImageFont.load_default(size=64)
    )
    buf = io.BytesIO()
    img.save(buf, "PDF")
    text = ocr.ocr_bytes(buf.getvalue(), ".pdf", tmp_path)
    assert "glucosa" in text.lower() and "92" in text


def _png(color="gray"):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, "PNG")
    return buf.getvalue()


def test_images_are_stored_encrypted_linked_to_their_report_and_deletable(client, tmp_path):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc = upload(c, me, make_pdf(REPORT.splitlines()), "Rx torax.pdf").json()["document_id"]
    rev = c.get(f"/api/documents/{doc}").json()
    day = rev["imaging"][0]["performed_on"]
    c.post(
        f"/api/documents/{doc}/review-imaging",
        json={
            "decisions": [{"position": 0, "accept": True, "performed_on": day, "study_name": "Rx de tórax"}]
        },
        headers=H,
    ).raise_for_status()

    def send(data, name):
        return c.post(f"/api/people/{me}/documents", files={"file": (name, data, "image/png")}, headers=H)

    mine = send(_png("red"), f"{day} - Rx de Torax - Imagen 1.png")
    assert mine.status_code == 200 and mine.json()["kind"] == "foto"
    other = send(_png("blue"), "12-03-1999 Rx de pie.png")
    assert send(_png("red"), "otra.png").status_code == 409  # mismo contenido
    assert c.post(
        f"/api/people/{me}/documents", files={"file": ("x.png", b"no soy imagen", "image/png")}, headers=H
    ).status_code in (415, 422)

    studies = c.get(f"/api/people/{me}/imaging").json()
    assert [i["title"] for i in studies[0]["images"]] == [f"{day} - Rx de Torax - Imagen 1"]
    loose = c.get(f"/api/people/{me}/images/loose").json()
    assert [i["title"] for i in loose] == ["12-03-1999 Rx de pie"]

    img = c.get(f"/api/images/{mine.json()['image_id']}/file")
    assert (
        img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == _png("red")
    )
    assert all(_png("red") not in p.read_bytes() for p in (tmp_path / "originals").glob("*.bin"))  # cifrado
    assert c.delete(f"/api/images/{other.json()['image_id']}", headers=H).status_code == 200
    assert c.get(f"/api/people/{me}/images/loose").json() == []


def test_image_dates_and_linking_rules():
    from house.app.ingest import _filename_date, link_images

    assert _filename_date("28-12-2022 Radiometria.jpg") == "2022-12-28"
    assert _filename_date("2026-02-06 - Rx de Abdomen - Imagen 1.png") == "2026-02-06"
    assert _filename_date("sin fecha.png") is None and _filename_date("31-02-2022 x.png") is None
    studies = [
        {
            "id": 1,
            "study_name": "Radiografia de abdomen",
            "modality": "Radiografía",
            "performed_on": "2026-02-06",
        },
        {
            "id": 2,
            "study_name": "Radiografia de torax",
            "modality": "Radiografía",
            "performed_on": "2026-02-06",
        },
    ]
    imgs = [
        {"id": 10, "title": "2026-02-06 - Rx de Abdomen - Imagen 2", "performed_on": "2026-02-06"},
        {
            "id": 11,
            "title": "2026-02-06 - Foto",
            "performed_on": "2026-02-06",
        },  # dos estudios ese día: ambigua
        {"id": 12, "title": "Sin fecha", "performed_on": None},
    ]
    linked, loose = link_images(imgs, studies)
    assert [i["id"] for i in linked[1]] == [10] and [i["id"] for i in loose] == [11, 12]


def test_related_studies_same_day_endoscopy_and_pathology_plus_manual_links():
    from house.app.ingest import related_studies

    def st(i, mod, day):
        return {"id": i, "study_name": f"E{i}", "modality": mod, "performed_on": day, "document_id": i}

    studies = [
        st(1, "Endoscopia", "2026-02-07"), st(2, "Endoscopia", "2026-02-07"), st(3, "Patología", "2026-02-07"),
        st(4, "Endoscopia", "2024-07-27"), st(5, "Patología", "2024-07-30"), st(6, "Radiografía", "2026-02-07"),
    ]  # fmt: skip
    rel = related_studies(studies, {(4, 5)})
    assert sorted(r["id"] for r in rel[1]) == [2, 3] and rel[6] == []  # una radiografía no se une sola
    assert [(r["id"], r["manual"]) for r in rel[4]] == [(5, True)] and [
        (r["id"], r["manual"]) for r in rel[5]
    ] == [(4, True)]
    assert all(not r["manual"] for r in rel[2])


def test_manual_study_links_through_the_api(client):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    ids = []
    for name in ("Rx torax.pdf", "Rx otro.pdf"):
        doc = upload(c, me, make_pdf(REPORT.splitlines() + [name]), name).json()["document_id"]
        rev = c.get(f"/api/documents/{doc}").json()
        c.post(f"/api/documents/{doc}/review-imaging", json={"decisions": [
            {"position": 0, "accept": True, "performed_on": f"2024-01-0{len(ids) + 1}", "study_name": f"Estudio {len(ids)}"},
            {"position": 1, "accept": False}]}, headers=H).raise_for_status()  # fmt: skip
        ids.append(rev["document"]["id"])
    a, b = [s["id"] for s in c.get(f"/api/people/{me}/imaging").json()]
    assert c.post(f"/api/imaging/{a}/links", json={"other_id": b}, headers=H).status_code == 200
    assert c.post(f"/api/imaging/{a}/links", json={"other_id": a}, headers=H).status_code == 422
    linked = {s["id"]: s["related"] for s in c.get(f"/api/people/{me}/imaging").json()}
    assert [r["id"] for r in linked[a]] == [b] and [r["id"] for r in linked[b]] == [a]
    assert c.delete(f"/api/imaging/{b}/links/{a}", headers=H).status_code == 200
    assert all(s["related"] == [] for s in c.get(f"/api/people/{me}/imaging").json())


def test_imaging_gaps_names_what_was_not_found():
    import json
    import sqlite3

    from house.app.ingest import IMAGING_SCHEMA, imaging_gaps

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(IMAGING_SCHEMA)
    db.execute("CREATE TABLE imaging_study(document_id, study_name, performed_on, conclusion)")
    drafts = [
        {"study_name": "Completo", "performed_on": "2026-02-06", "conclusion": "Sin hallazgos."},
        {"study_name": "Sin fecha", "performed_on": None, "conclusion": "Normal."},
        {"study_name": "Sin nada", "performed_on": None, "conclusion": ""},
    ]
    for pos, d in enumerate(drafts):
        db.execute(
            "INSERT INTO imaging_draft(document_id, position, data) VALUES(1, ?, ?)", (pos, json.dumps(d))
        )
    gaps = imaging_gaps(db, 1, reviewed=False)
    assert [(g["study_name"], len(g["missing"])) for g in gaps] == [("Sin fecha", 1), ("Sin nada", 2)]
    assert imaging_gaps(db, 2, reviewed=True) == []
