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


def test_problems_link_to_treatments_and_surgeries_and_old_tables_are_upgraded(world):
    c, me, _ = world
    problem = add(c, me, "problem", name="Prediabetes", status="Seguimiento").json()["id"]
    med = add(
        c, me, "medication", name="Metformina", dose="850 mg al día", reason="Prediabetes", since_year="2024"
    ).json()["id"]
    old = add(c, me, "medication", name="Otra", active=False, until_year="2020").json()["id"]
    surgery = add(c, me, "procedure", name="Bypass gástrico", year="2019").json()["id"]
    cand = c.get(f"/api/people/{me}/link-candidates").json()
    assert [(m["title"], m["reason"]) for m in cand["medications"]][0] == (
        "Metformina · 850 mg al día",
        "Prediabetes",
    )
    assert [p["title"] for p in cand["procedures"]] == ["Bypass gástrico"]

    url = f"/api/people/{me}/clinical/problem/{problem}/links"
    for kind, ref in (("medication", med), ("medication", old), ("procedure", surgery)):
        assert c.post(url, json={"kind": kind, "ref": str(ref)}, headers=H).status_code == 200
    (p,) = c.get(f"/api/people/{me}/clinical").json()["problems"]
    by = {ln["title"]: ln for ln in p["links"]}
    assert (
        by["Metformina"]["value"] == "850 mg al día"
        and by["Metformina"]["extra"] == "desde 2024"
        and by["Metformina"]["active"]
    )
    assert (
        by["Otra"]["active"] is False
        and by["Otra"]["extra"] == "hasta 2020"
        and by["Bypass gástrico"]["extra"] == "2019"
    )
    meds = {m["name"]: m for m in c.get(f"/api/people/{me}/clinical").json()["medications"]}
    assert meds["Metformina"]["problems"] == [{"id": problem, "name": "Prediabetes"}]
    assert c.post(url, json={"kind": "medication", "ref": "9999"}, headers=H).status_code == 404
    # quitar el medicamento limpia la relación
    c.delete(f"/api/people/{me}/clinical/medication/{med}", headers=H).raise_for_status()
    (p,) = c.get(f"/api/people/{me}/clinical").json()["problems"]
    assert "Metformina" not in [ln["title"] for ln in p["links"]]

    # base anterior: la tabla solo admitía tres tipos; se reconstruye sin perder lo ya ligado
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE person(id INTEGER PRIMARY KEY); CREATE TABLE problem(id INTEGER PRIMARY KEY, person_id, name, status);"
        "CREATE TABLE problem_link(id INTEGER PRIMARY KEY, problem_id INTEGER NOT NULL REFERENCES problem(id) ON DELETE CASCADE, "
        "kind TEXT NOT NULL CHECK (kind IN ('document', 'imaging', 'analyte')), ref TEXT NOT NULL, UNIQUE (problem_id, kind, ref));"
        "INSERT INTO problem(id, person_id, name, status) VALUES (1, 1, 'X', 'En control');"
        "INSERT INTO problem_link(problem_id, kind, ref) VALUES (1, 'analyte', 'glucose'), (1, 'imaging', '4');"
    )
    clinical.migrate(db)
    assert [tuple(r) for r in db.execute("SELECT kind, ref FROM problem_link ORDER BY id")] == [
        ("analyte", "glucose"),
        ("imaging", "4"),
    ]
    db.execute(
        "INSERT INTO problem_link(problem_id, kind, ref) VALUES (1, 'medication', '1')"
    )  # ya se admite
    clinical.migrate(db)  # idempotente
    assert db.execute("SELECT COUNT(*) FROM problem_link").fetchone()[0] == 3


def test_supplements_are_kept_apart_from_medications(world):
    c, me, member = world
    r = add(
        c, me, "supplement", name="Proteína (whey)", dose="1 scoop al día", brand="Marca X", since_year="2024"
    )
    assert r.status_code == 200
    sid = r.json()["id"]
    add(
        c, me, "supplement", name="Omega 3", active=False, since_year="2022", until_year="2023"
    ).raise_for_status()
    assert add(c, me, "supplement", name="Creatina", since_year="2025", until_year="2020").status_code == 422
    assert add(c, me, "supplement", name="").status_code == 422
    add(c, me, "medication", name="Metformina").raise_for_status()

    o = c.get(f"/api/people/{me}/clinical").json()
    assert [(s["name"], s["active"]) for s in o["supplements"]] == [("Proteína (whey)", 1), ("Omega 3", 0)]
    assert [m["name"] for m in o["medications"]] == [
        "Metformina"
    ]  # los suplementos no se mezclan con los medicamentos
    assert "Creatina monohidratada" in c.get("/api/clinical/suggestions").json()["supplement"]

    up = c.put(
        f"/api/people/{me}/clinical/supplement/{sid}",
        json={"name": "Proteína", "dose": "2 scoops"},
        headers=H,
    )
    assert up.status_code == 200
    assert c.get(f"/api/people/{me}/clinical").json()["supplements"][0]["dose"] == "2 scoops"
    # cada persona ve lo suyo
    assert c.get(f"/api/people/{member}/clinical").json()["supplements"] == []
    assert c.delete(f"/api/people/{member}/clinical/supplement/{sid}", headers=H).status_code == 404
    assert c.delete(f"/api/people/{me}/clinical/supplement/{sid}", headers=H).status_code == 200


def test_medications_have_active_ingredient_and_brand(world):
    c, me, _ = world
    both = add(c, me, "medication", brand="Glucophage", active_ingredient="metformina", dose="850 mg").json()[
        "id"
    ]
    only_brand = add(c, me, "medication", brand="Aspirina Protect").json()["id"]
    only_active = add(c, me, "medication", active_ingredient="Omeprazol").json()["id"]
    legacy = add(c, me, "medication", name="Losartán").json()["id"]  # datos anteriores: solo «nombre»
    assert add(c, me, "medication", dose="1 al día").status_code == 422  # sin sustancia activa ni marca

    meds = {m["id"]: m for m in c.get(f"/api/people/{me}/clinical").json()["medications"]}
    assert (meds[both]["name"], meds[both]["brand"], meds[both]["active_ingredient"]) == (
        "metformina",
        "Glucophage",
        "metformina",
    )
    assert meds[only_brand]["name"] == "Aspirina Protect" and meds[only_brand]["active_ingredient"] is None
    assert meds[only_active]["name"] == "Omeprazol" and meds[legacy]["name"] == "Losartán"

    up = c.put(
        f"/api/people/{me}/clinical/medication/{only_brand}",
        json={"brand": "Aspirina Protect", "active_ingredient": "ácido acetilsalicílico"},
        headers=H,
    )
    assert up.status_code == 200
    assert c.get(f"/api/people/{me}/clinical").json()["medications"][0]["name"] in (
        "ácido acetilsalicílico",
        "metformina",
        "Omeprazol",
        "Losartán",
    )
    # aceptar una sugerencia: el nombre que había pasa a ser el comercial si era distinto
    assert (
        c.put(
            f"/api/people/{me}/medications/{legacy}/ingredient",
            json={"active_ingredient": "losartán"},
            headers=H,
        ).status_code
        == 200
    )
    assert (
        c.put(
            f"/api/people/{me}/medications/{only_active}/ingredient",
            json={"active_ingredient": "omeprazol"},
            headers=H,
        ).status_code
        == 200
    )
    m = {x["id"]: x for x in c.get(f"/api/people/{me}/clinical").json()["medications"]}
    assert (
        m[legacy]["active_ingredient"] == "losartán" and m[legacy]["brand"] is None
    )  # era la misma sustancia
    assert m[only_active]["active_ingredient"] == "omeprazol"
    assert (
        c.put(
            f"/api/people/{me}/medications/999/ingredient", json={"active_ingredient": "x"}, headers=H
        ).status_code
        == 404
    )


def test_allergies_have_a_type_and_no_allergies_means_no_drug_allergies(world):
    c, me, _ = world
    assert c.get(f"/api/people/{me}/clinical").json()["allergy_categories"] == [
        "Medicamento",
        "Alimento",
        "Ambiental",
        "Otro",
    ]
    assert (
        c.put(f"/api/people/{me}/clinical-none/allergy", headers=H).status_code == 200
    )  # «sin alergias a medicamentos»
    # una alergia ambiental (prueba cutánea) no contradice esa confirmación
    add(c, me, "allergy", substance="Dermatophagoides pteronyssinus", category="Ambiental").raise_for_status()
    add(c, me, "allergy", substance="Cacahuate", category="Alimento").raise_for_status()
    assert "allergy" in c.get(f"/api/people/{me}/clinical").json()["none"]
    # una alergia a un medicamento sí la invalida
    add(
        c, me, "allergy", substance="Penicilina", category="Medicamento", reaction="Ronchas"
    ).raise_for_status()
    assert "allergy" not in c.get(f"/api/people/{me}/clinical").json()["none"]
    assert add(c, me, "allergy", substance="X", category="Cosa").status_code == 422
    cats = [(a["substance"], a["category"]) for a in c.get(f"/api/people/{me}/clinical").json()["allergies"]]
    assert ("Penicilina", "Medicamento") in cats and ("Cacahuate", "Alimento") in cats

    # solo con alergias no medicamentosas se puede confirmar «sin alergias a medicamentos»
    for a in c.get(f"/api/people/{me}/clinical").json()["allergies"]:
        if a["category"] == "Medicamento":
            c.delete(f"/api/people/{me}/clinical/allergy/{a['id']}", headers=H)
    assert c.put(f"/api/people/{me}/clinical-none/allergy", headers=H).status_code == 200
    add(c, me, "allergy", substance="Amoxicilina").raise_for_status()  # sin tipo: se trata como medicamento
    assert "allergy" not in c.get(f"/api/people/{me}/clinical").json()["none"]


def test_sintomas_y_reaccion_a_medicamento(world):
    c, me, _ = world
    r = add(c, me, "symptom", occurred_on="2026-03-01", what="Sangre en las heces", related="ibuprofeno")
    r.raise_for_status()
    assert add(c, me, "symptom", occurred_on="2999-01-01", what="x").status_code == 422  # no puede ser futuro
    assert add(c, me, "symptom", occurred_on="2026-03-01", what="  ").status_code == 422
    add(
        c,
        me,
        "medication",
        active_ingredient="ibuprofeno",
        active=False,
        bad_reaction="Al día siguiente hay sangre en mis heces",
    ).raise_for_status()
    data = c.get(f"/api/people/{me}/clinical").json()
    assert data["symptoms"][0]["what"] == "Sangre en las heces"
    assert data["medications"][0]["bad_reaction"].startswith("Al día siguiente")
    assert any(e["kind"] == "sintoma" and "ibuprofeno" in e["subtitle"] for e in data["timeline"])
    c.delete(f"/api/people/{me}/clinical/symptom/{data['symptoms'][0]['id']}", headers=H).raise_for_status()
    assert c.get(f"/api/people/{me}/clinical").json()["symptoms"] == []


def test_notas_largas_de_padecimiento(world):
    c, me, _ = world
    texto = "Primera línea.\n\n" + "x" * 3000
    add(c, me, "problem", name="Prueba", status="En control", notes=texto).raise_for_status()
    assert (
        c.get(f"/api/people/{me}/clinical").json()["problems"][0]["notes"] == texto
    )  # conserva los saltos de línea
    assert add(c, me, "problem", name="Otra", status="En control", notes="x" * 4001).status_code == 422


def test_consulta_con_nota_larga(world):
    c, me, _ = world
    texto = "Resumen\n" + "y" * 5000
    add(c, me, "consultation", occurred_on="2026-02-07", reason="Explicación", notes=texto).raise_for_status()
    assert c.get(f"/api/people/{me}/clinical").json()["consultations"][0]["notes"] == texto
