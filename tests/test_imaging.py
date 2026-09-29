"""Informes de imagen: lectura de secciones, revisión, migración y borrado (datos inventados)."""

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
