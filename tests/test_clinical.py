"""Expediente clínico: captura manual, validación, permisos e historial cronológico (datos inventados)."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from house.app import clinical
from house.app.api import create_app
from house.config import Config, ProviderConfig
from house.providers import Router
from test_app import ADMIN, KEY, H, make_pdf, upload
from test_imaging import REPORT


@pytest.fixture
def world(tmp_path):
    cfg = Config(tasks={"extract": "base"}, providers={"base": ProviderConfig(name="base", kind="mock")})
    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    admin_id = c.get("/api/me").json()["id"]
    member = {"display_name": "Miembro", "birth_date": "1992-02-02", "sex_at_birth": "F", "pin": "2468"}
    member_id = c.post("/api/people", json=member, headers=H).json()["id"]
    return c, admin_id, member_id


def add(c, pid, kind, **data):
    return c.post(f"/api/people/{pid}/clinical/{kind}", json=data, headers=H)


def test_add_edit_delete_each_kind_and_ordering(world):
    c, me, _ = world
    add(c, me, "allergy", substance="Penicilina", reaction="Erupción").raise_for_status()
    add(c, me, "problem", name="Dislipidemia", status="En control", since_year="2021").raise_for_status()
    add(c, me, "problem", name="Anemia", status="Resuelta", since_year="2019").raise_for_status()
    add(
        c,
        me,
        "medication",
        name="Vitamina D3",
        dose="2,000 UI al día",
        reason="Deficiencia",
        since_year="2022",
    )
    add(c, me, "medication", name="Amoxicilina", active=False, since_year="2020", until_year="2020")
    add(c, me, "family", relative="Padre", condition="Hipertensión").raise_for_status()
    add(c, me, "procedure", name="Apendicectomía", year="2009").raise_for_status()
    add(
        c, me, "vaccine", name="Influenza estacional", given_on="2025-10-15", place="Farmacia"
    ).raise_for_status()
    add(
        c, me, "consultation", occurred_on="2026-02-01", reason="Chequeo anual", doctor="Dra. Inventada"
    ).raise_for_status()

    o = c.get(f"/api/people/{me}/clinical").json()
    assert [a["substance"] for a in o["allergies"]] == ["Penicilina"]
    assert [p["name"] for p in o["problems"]] == ["Dislipidemia", "Anemia"]  # lo resuelto al final
    assert [m["name"] for m in o["medications"]] == ["Vitamina D3", "Amoxicilina"]  # actuales primero
    assert o["family"][0]["condition"] == "Hipertensión" and o["procedures"][0]["year"] == "2009"

    item = o["allergies"][0]["id"]
    c.put(
        f"/api/people/{me}/clinical/allergy/{item}",
        json={"substance": "Penicilina", "reaction": "Urticaria"},
        headers=H,
    )
    assert c.get(f"/api/people/{me}/clinical").json()["allergies"][0]["reaction"] == "Urticaria"
    c.delete(f"/api/people/{me}/clinical/allergy/{item}", headers=H).raise_for_status()
    assert c.get(f"/api/people/{me}/clinical").json()["allergies"] == []
    assert c.delete(f"/api/people/{me}/clinical/allergy/{item}", headers=H).status_code == 404


def test_validation(world):
    c, me, _ = world
    assert add(c, me, "allergy", substance="  ").status_code == 422
    assert add(c, me, "problem", name="X", status="Inventado").status_code == 422
    assert add(c, me, "problem", name="X", status="En control", since_year="hace mucho").status_code == 422
    assert (
        add(
            c, me, "problem", name="X", status="En control", since_year=str(date.today().year + 1)
        ).status_code
        == 422
    )
    assert add(c, me, "vaccine", name="Influenza", given_on="2999-01-01").status_code == 422
    assert add(c, me, "vaccine", name="Influenza", given_on="15/10/2025").status_code == 422
    assert add(c, me, "medication", name="Z", since_year="2022", until_year="2020").status_code == 422
    assert add(c, me, "allergy", substance="x" * 300).status_code == 422
    assert add(c, me, "cosa_rara", name="x").status_code == 404
    assert (
        c.put(f"/api/people/{me}/clinical/allergy/999", json={"substance": "A"}, headers=H).status_code == 404
    )


def test_confirming_none_and_duplicates(world):
    c, me, _ = world
    assert c.put(f"/api/people/{me}/clinical-none/allergy", headers=H).status_code == 200
    assert "allergy" in c.get(f"/api/people/{me}/clinical").json()["none"]
    add(c, me, "allergy", substance="Látex").raise_for_status()  # ya hay una: deja de valer "sin alergias"
    assert "allergy" not in c.get(f"/api/people/{me}/clinical").json()["none"]
    assert c.put(f"/api/people/{me}/clinical-none/allergy", headers=H).status_code == 409
    assert c.put(f"/api/people/{me}/clinical-none/inventada", headers=H).status_code == 404

    add(c, me, "medication", name="Losartán", dose="50 mg").raise_for_status()
    add(
        c, me, "medication", name="losartan", dose="100 mg"
    ).raise_for_status()  # mismo fármaco, otra escritura
    add(c, me, "medication", name="Omeprazol").raise_for_status()
    dup = {m["name"]: m["duplicate"] for m in c.get(f"/api/people/{me}/clinical").json()["medications"]}
    assert dup == {"Losartán": True, "losartan": True, "Omeprazol": False}


def test_timeline_merges_studies_imaging_and_manual_events(world):
    c, me, _ = world
    doc = upload(
        c,
        me,
        make_pdf(
            [
                "Informe de Resultados de Laboratorio",
                "Fecha de Toma : 01/03/2026",
                "Glucosa 105 70 - 99 mg/dL",
            ]
        ),
    )
    doc_id = doc.json()["document_id"]
    rows = c.get(f"/api/documents/{doc_id}").json()["rows"]
    c.post(
        f"/api/documents/{doc_id}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": [{"row_id": rows[0]["id"], "accept": True}]},
    )
    img = upload(c, me, make_pdf(REPORT.splitlines()), "Rx.pdf").json()["document_id"]
    c.post(
        f"/api/documents/{img}/review-imaging",
        headers=H,
        json={"decisions": [{"position": 0, "accept": True}, {"position": 1, "accept": False}]},
    )
    add(c, me, "consultation", occurred_on="2026-02-01", reason="Chequeo").raise_for_status()
    add(c, me, "vaccine", name="Influenza", given_on="2025-10-15").raise_for_status()
    add(c, me, "procedure", name="Apendicectomía", year="2009").raise_for_status()
    add(c, me, "procedure", name="Sin año").raise_for_status()  # sin año: no cabe en la línea de tiempo

    tl = c.get(f"/api/people/{me}/clinical").json()["timeline"]
    assert [(e["kind"], e["date"]) for e in tl] == [
        ("laboratorio", "2026-03-01"),
        ("consulta", "2026-02-01"),
        ("vacuna", "2025-10-15"),
        ("imagen", "2025-05-04"),
        ("cirugia", "2009-01-01"),
    ]
    lab = tl[0]
    assert lab["subtitle"] == "1 resultados, 1 fuera de rango" and lab["ref"] == {
        "type": "document",
        "id": doc_id,
    }
    assert (
        tl[3]["title"] == "Torax Oseo AP y Lateral" and tl[3]["ref"]["id"] == img and tl[4]["approx"] is True
    )


def test_permissions_and_access_log(world):
    c, admin_id, member_id = world
    add(
        c, member_id, "allergy", substance="Polen"
    ).raise_for_status()  # el admin edita a otro: queda registrado
    c.post("/api/logout", headers=H)
    c.post("/api/login", json={"person_id": member_id, "pin": "2468"}, headers=H).raise_for_status()
    assert [a["substance"] for a in c.get(f"/api/people/{member_id}/clinical").json()["allergies"]] == [
        "Polen"
    ]
    add(c, member_id, "allergy", substance="Látex").raise_for_status()  # cada persona edita el suyo
    assert c.get(f"/api/people/{admin_id}/clinical").status_code == 403
    assert add(c, admin_id, "allergy", substance="X").status_code == 403
    assert c.delete(f"/api/people/{admin_id}/clinical/allergy/1", headers=H).status_code == 403
    log = c.get("/api/access-log").json()
    assert [(e["actor"], e["action"]) for e in log] == [("Admin Ejemplo", "editar_expediente")]
    assert "Penicilina" in c.get("/api/clinical/suggestions").json()["allergy"]


def test_deleting_a_person_deletes_their_clinical_data(world):
    c, me, member_id = world
    add(c, member_id, "allergy", substance="Polen").raise_for_status()
    add(c, member_id, "vaccine", name="Influenza", given_on="2025-10-15").raise_for_status()
    c.delete(f"/api/people/{member_id}", headers=H).raise_for_status()
    db = c.app.state.db
    for table in ("allergy", "vaccine"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_migration_adds_columns_to_older_tables(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.row_factory = sqlite3.Row
    db.executescript(
        "PRAGMA foreign_keys=OFF; CREATE TABLE person(id INTEGER PRIMARY KEY);"
        "CREATE TABLE medication(id INTEGER PRIMARY KEY, person_id INTEGER, name TEXT NOT NULL, dose TEXT,"
        " reason TEXT, since_year TEXT, active INTEGER NOT NULL DEFAULT 1);"
        "INSERT INTO medication(person_id, name) VALUES (1, 'Losartán');"
    )
    clinical.migrate(db)
    clinical.migrate(db)  # idempotente
    cols = {r["name"] for r in db.execute("PRAGMA table_info(medication)")}
    assert {"prescriber", "until_year", "notes"} <= cols
    assert db.execute("SELECT name FROM medication").fetchone()[0] == "Losartán"  # nada se pierde


def test_only_suspended_medications_still_allow_confirming_no_current_ones(world):
    c, me, _ = world
    add(
        c, me, "medication", name="Amoxicilina", active=False, since_year="2020", until_year="2020"
    ).raise_for_status()
    assert c.put(f"/api/people/{me}/clinical-none/medication", headers=H).status_code == 200
    add(
        c, me, "medication", name="Otra suspendida", active=False
    ).raise_for_status()  # sigue sin haber actuales
    assert "medication" in c.get(f"/api/people/{me}/clinical").json()["none"]
    add(c, me, "medication", name="Losartán").raise_for_status()  # ahora sí hay uno actual
    assert "medication" not in c.get(f"/api/people/{me}/clinical").json()["none"]


def test_vaccine_brand_and_lot_dose_menu_and_one_time_brand_split(tmp_path):
    import sqlite3

    from house.app import clinical

    # base anterior: sin columnas de marca ni lote, con la marca metida en el nombre
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE person(id INTEGER PRIMARY KEY);"
        "CREATE TABLE vaccine(id INTEGER PRIMARY KEY, person_id INTEGER, name TEXT NOT NULL, given_on TEXT NOT NULL,"
        " dose_label TEXT, place TEXT, notes TEXT);"
        "INSERT INTO vaccine(person_id, name, given_on) VALUES"
        " (1, 'COVID-19 Pfizer', '2021-05-20'), (1, 'Influenza estacional Vaxigrip Tetra', '2025-12-20'),"
        " (1, 'Tétanos (Td)', '2020-01-01'), (1, 'Vacuna rara', '2019-01-01');"
    )
    clinical.migrate(db)
    got = {r["given_on"]: (r["name"], r["brand"]) for r in db.execute("SELECT * FROM vaccine")}
    assert got == {
        "2021-05-20": ("COVID-19", "Pfizer"),
        "2025-12-20": ("Influenza estacional", "Vaxigrip Tetra"),
        "2020-01-01": ("Tétanos (Td)", None),
        "2019-01-01": ("Vacuna rara", None),
    }
    db.execute("UPDATE vaccine SET name = 'COVID-19 Pfizer', brand = NULL WHERE given_on = '2021-05-20'")
    clinical.migrate(db)  # solo una vez: lo que la persona escriba después no se reinterpreta
    assert (
        db.execute("SELECT name FROM vaccine WHERE given_on = '2021-05-20'").fetchone()["name"]
        == "COVID-19 Pfizer"
    )
    assert "Refuerzo" in clinical.DOSE_OPTIONS and "Primera dosis" in clinical.DOSE_OPTIONS


def _lab(c, pid, value="105"):
    lines = [
        "Informe de Resultados de Laboratorio",
        "Fecha de Toma : 01/03/2026",
        f"Glucosa {value} 70 - 99 mg/dL",
    ]
    doc = upload(c, pid, make_pdf(lines), "Perfil.pdf").json()["document_id"]
    rows = c.get(f"/api/documents/{doc}").json()["rows"]
    body = {"collected_on": "2026-03-01", "decisions": [{"row_id": r["id"], "accept": True} for r in rows]}
    c.post(f"/api/documents/{doc}/review", json=body, headers=H).raise_for_status()
    return doc


def _report(c, pid):
    doc = upload(c, pid, make_pdf(REPORT.splitlines()), "Rx torax.pdf").json()["document_id"]
    dec = [{"position": 0, "accept": True, "performed_on": "2026-02-06", "study_name": "Rx de tórax"}]
    c.post(f"/api/documents/{doc}/review-imaging", json={"decisions": dec}, headers=H).raise_for_status()
    return c.get(f"/api/people/{pid}/imaging").json()[0]["id"]


def test_problems_can_be_linked_to_studies_reports_and_analytes(world):
    c, me, member = world
    problem = add(c, me, "problem", name="Prediabetes", status="Seguimiento").json()["id"]
    doc, study = _lab(c, me), _report(c, me)
    cand = c.get(f"/api/people/{me}/link-candidates").json()
    assert [d["ref"] for d in cand["documents"]] == [str(doc)] and [i["ref"] for i in cand["imaging"]] == [
        str(study)
    ]
    assert [a["ref"] for a in cand["analytes"]] == ["glucose"]

    link = lambda kind, ref: c.post(  # noqa: E731
        f"/api/people/{me}/clinical/problem/{problem}/links", json={"kind": kind, "ref": ref}, headers=H
    )
    ids = [
        link("document", str(doc)).json()["id"],
        link("imaging", str(study)).json()["id"],
        link("analyte", "glucose").json()["id"],
    ]
    assert link("analyte", "glucose").json()["id"] == ids[2]  # ligar dos veces no duplica

    (p,) = c.get(f"/api/people/{me}/clinical").json()["problems"]
    by_kind = {ln["kind"]: ln for ln in p["links"]}
    assert by_kind["document"]["title"] == "Perfil" and by_kind["document"]["document_id"] == doc
    assert by_kind["imaging"]["title"] == "Rx de tórax"
    assert by_kind["analyte"]["title"] == "Glucosa en ayunas" and "105" in by_kind["analyte"]["value"]
    # el informe sabe a qué padecimiento está ligado
    assert c.get(f"/api/people/{me}/imaging").json()[0]["problems"] == [
        {"id": problem, "name": "Prediabetes"}
    ]

    # no se puede ligar lo de otra persona, algo inexistente ni un tipo raro
    other = add(c, member, "problem", name="Asma", status="En control").json()["id"]

    def bad(pid, kind, ref, target=member):
        url = f"/api/people/{target}/clinical/problem/{pid}/links"
        return c.post(url, json={"kind": kind, "ref": ref}, headers=H).status_code

    assert bad(other, "document", str(doc)) == 404 and bad(other, "analyte", "glucose") == 404
    assert bad(problem, "imaging", str(study), me) == 200 and bad(problem, "imaging", "9999", me) == 404
    assert bad(problem, "cosa", "1", me) == 422
    assert (
        c.post(
            f"/api/people/{me}/clinical/problem/{other}/links",
            json={"kind": "analyte", "ref": "glucose"},
            headers=H,
        ).status_code
        == 404
    )

    # quitar la relación; y si se borra el estudio, la relación desaparece sola
    assert (
        c.delete(f"/api/people/{me}/clinical/problem/{problem}/links/{ids[2]}", headers=H).status_code == 200
    )
    assert (
        c.delete(f"/api/people/{me}/clinical/problem/{problem}/links/{ids[2]}", headers=H).status_code == 404
    )
    c.delete(f"/api/documents/{doc}", headers=H).raise_for_status()
    (p,) = c.get(f"/api/people/{me}/clinical").json()["problems"]
    assert [ln["kind"] for ln in p["links"]] == ["imaging"]
    c.delete(
        f"/api/people/{me}/clinical/problem/{problem}", headers=H
    )  # borrar el padecimiento limpia sus relaciones
    assert c.get(f"/api/people/{me}/imaging").json()[0]["problems"] == []
