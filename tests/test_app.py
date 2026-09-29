"""App local: primer uso, PIN, permisos entre perfiles, bitácora de accesos y cifrado (datos inventados)."""

import pytest
from fastapi.testclient import TestClient

from house.app import auth
from house.app.api import create_app
from house.app.vault import Vault

KEY = b"k" * 32
H = {"x-house": "1"}
ADMIN = {"display_name": "Admin Ejemplo", "birth_date": "1990-01-01", "sex_at_birth": "M", "pin": "123456"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path, key_provider=lambda: KEY))


def setup_family(client):
    assert client.get("/api/status").json() == {"needs_setup": True}
    client.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    member = {
        "display_name": "Miembro Ejemplo",
        "birth_date": "1992-02-02",
        "sex_at_birth": "F",
        "pin": "2468",
    }
    mid = client.post("/api/people", json=member, headers=H).json()["id"]
    managed = {
        "display_name": "Sin acceso",
        "birth_date": "1950-03-03",
        "sex_at_birth": "F",
        "has_login": False,
    }
    oid = client.post("/api/people", json=managed, headers=H).json()["id"]
    return mid, oid


def test_first_run_creates_admin_once(client):
    setup_family(client)
    assert client.get("/api/status").json() == {"needs_setup": False}
    assert client.post("/api/setup", json=ADMIN, headers=H).status_code == 409
    assert client.get("/api/me").json()["is_admin"] is True


def test_profiles_screen_lists_only_people_with_login(client):
    setup_family(client)
    names = [p["display_name"] for p in client.get("/api/profiles").json()]
    assert names == ["Admin Ejemplo", "Miembro Ejemplo"]


def test_member_sees_only_own_profile_and_admin_access_is_logged(client):
    mid, oid = setup_family(client)
    admin_id = client.get("/api/me").json()["id"]
    client.post("/api/logout", headers=H)
    assert client.get("/api/me").status_code == 401

    client.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H).raise_for_status()
    assert [p["id"] for p in client.get("/api/people").json()] == [mid]
    assert client.get(f"/api/people/{admin_id}/observations").status_code == 403
    assert client.get(f"/api/people/{mid}/observations").status_code == 200
    assert client.post("/api/people", json=ADMIN, headers=H).status_code == 403
    assert client.get("/api/access-log").json() == []

    client.post("/api/logout", headers=H)
    client.post("/api/login", json={"person_id": admin_id, "pin": "123456"}, headers=H).raise_for_status()
    assert client.get(f"/api/people/{mid}/observations").status_code == 200
    client.post("/api/logout", headers=H)
    client.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H).raise_for_status()
    log = client.get("/api/access-log").json()
    assert [(e["actor"], e["action"]) for e in log] == [("Admin Ejemplo", "ver_resultados")]


def test_person_without_login_cannot_sign_in(client):
    _, oid = setup_family(client)
    client.post("/api/logout", headers=H)
    assert client.post("/api/login", json={"person_id": oid, "pin": "0000"}, headers=H).status_code == 401


def test_lockout_after_five_wrong_pins(client):
    mid, _ = setup_family(client)
    client.post("/api/logout", headers=H)
    for _ in range(5):
        r = client.post("/api/login", json={"person_id": mid, "pin": "9999"}, headers=H)
        assert r.status_code == 401
    r = client.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H)
    assert r.status_code == 401 and "Espera" in r.json()["detail"]


def test_admin_resets_pin_and_deletes_profile(client):
    mid, _ = setup_family(client)
    client.put(f"/api/people/{mid}/pin", json={"pin": "1357"}, headers=H).raise_for_status()
    assert client.put(f"/api/people/{mid}/pin", json={"pin": "12"}, headers=H).status_code == 422
    client.delete(f"/api/people/{mid}", headers=H).raise_for_status()
    assert mid not in [p["id"] for p in client.get("/api/people").json()]
    me = client.get("/api/me").json()["id"]
    assert client.delete(f"/api/people/{me}", headers=H).status_code == 409


def test_writes_need_app_header_and_local_host(client):
    assert client.post("/api/setup", json=ADMIN).status_code == 403
    assert client.get("/api/status", headers={"host": "malicioso.example"}).status_code == 400


def test_idle_session_expires(tmp_path):
    app = create_app(tmp_path, key_provider=lambda: KEY)
    db = app.state.db
    db.execute(
        "INSERT INTO person(display_name,birth_date,sex_at_birth,is_admin,pin_hash) VALUES('A','1990-01-01','M',1,?)",
        (auth.hash_pin("1234"),),
    )
    token = auth.login(db, 1, "1234", now=0)
    assert auth.current_person(db, token, now=60) is not None
    assert auth.current_person(db, token, now=60 + auth.IDLE_SECONDS + 1) is None


def test_vault_encrypts_originals(tmp_path):
    v = Vault(tmp_path, key_provider=lambda: KEY)
    name = v.put(b"%PDF-1.4 contenido inventado")
    assert b"contenido inventado" not in (tmp_path / name).read_bytes()
    assert v.get(name) == b"%PDF-1.4 contenido inventado"
    v.delete(name)
    assert not (tmp_path / name).exists()


# --- Subir y revisar estudios (PDF generado aquí, datos inventados) ---

from house.config import Config, ProviderConfig  # noqa: E402
from house.providers import Router  # noqa: E402

LAB_LINES = [
    "Paciente: PEREZ FICTICIO JUAN",
    "Fecha de Toma : 01/03/2026",
    "QUIMICA",
    "Glucosa 105 70 - 99 mg/dL",
    "Colesterol HDL 45 40 - 60 mg/dL",
    "Metodo: Fotometria automatizada",
    "EXAMEN GENERAL DE ORINA",
    "Nitritos Positivo Negativo",
    "pH 6.0 5.0 - 7.0",
]


def make_pdf(lines):
    """PDF mínimo con texto real (sin dependencias)."""
    content = (
        "BT /F1 11 Tf 50 750 Td 14 TL "
        + " ".join("(" + ln.replace("(", r"\(").replace(")", r"\)") + ") Tj T*" for ln in lines)
        + " ET"
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


@pytest.fixture
def lab_client(tmp_path):
    cfg = Config(tasks={"extract": "base"}, providers={"base": ProviderConfig(name="base", kind="mock")})
    return TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg)))


def upload(client, pid, data, name="Estudio marzo.pdf"):
    return client.post(
        f"/api/people/{pid}/documents", files={"file": (name, data, "application/pdf")}, headers=H
    )


def test_upload_review_and_confirm(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    pid = c.post(
        "/api/people",
        json={
            "display_name": "Juan",
            "birth_date": "1980-05-05",
            "sex_at_birth": "M",
            "has_login": False,
            "other_names": ["PEREZ FICTICIO JUAN"],
        },
        headers=H,
    ).json()["id"]
    pdf = make_pdf(LAB_LINES)
    r = upload(c, pid, pdf)
    assert r.status_code == 200, r.text
    doc_id = r.json()["document_id"]
    assert upload(c, pid, pdf).status_code == 409  # mismo archivo

    rev = c.get(f"/api/documents/{doc_id}").json()
    assert "PEREZ FICTICIO" not in rev["ai_saw"]
    assert rev["document"]["collected_on"] == "2026-03-01"
    by = {row["analyte_key"]: row for row in rev["rows"]}
    assert by["glucose"]["value_num"] == 105 and by["glucose"]["status"] == "high"
    assert by["urine_nitrite"]["value_text"] == "Positivo" and by["urine_nitrite"]["status"] == "abnormal"
    assert c.get(f"/api/documents/{doc_id}/file").content == pdf

    decisions = [
        {"row_id": by["glucose"]["id"], "accept": True, "value_num": 98},  # corrige el valor
        {"row_id": by["urine_nitrite"]["id"], "accept": True},
        {"row_id": by["hdl"]["id"], "accept": False},
    ]
    r = c.post(
        f"/api/documents/{doc_id}/review",
        json={"collected_on": "2026-03-01", "decisions": decisions},
        headers=H,
    )
    assert r.json() == {"saved": 2}
    obs = {o["analyte_key"]: o for o in c.get(f"/api/people/{pid}/observations").json()}
    assert set(obs) == {"glucose", "urine_nitrite"}
    assert obs["glucose"]["value_num"] == 98 and obs["glucose"]["status"] == "ok"
    assert obs["glucose"]["method"] == "Fotometria automatizada"
    assert obs["urine_nitrite"]["status"] == "abnormal"
    again = c.post(
        f"/api/documents/{doc_id}/review", json={"collected_on": "2026-03-01", "decisions": []}, headers=H
    )
    assert again.status_code == 409

    c.delete(f"/api/documents/{doc_id}", headers=H).raise_for_status()
    assert c.get(f"/api/people/{pid}/observations").json() == []


def test_upload_rejects_non_pdf_and_scans(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    assert upload(c, me, b"hola", "nota.txt").status_code == 415
    assert upload(c, me, make_pdf([]), "escaneo.pdf").status_code == 422


def test_member_cannot_open_others_documents(lab_client):
    c = lab_client
    mid, _ = setup_family(c)
    admin_id = c.get("/api/me").json()["id"]
    doc_id = upload(c, admin_id, make_pdf(LAB_LINES)).json()["document_id"]
    c.post("/api/logout", headers=H)
    c.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H).raise_for_status()
    assert c.get(f"/api/documents/{doc_id}").status_code == 403
    assert c.get(f"/api/documents/{doc_id}/file").status_code == 403
    assert upload(c, admin_id, make_pdf(["Glucosa 90 70 - 99 mg/dL"])).status_code == 403


def test_settings_key_switches_reader_and_is_stored_encrypted(tmp_path):
    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY))
    c.post("/api/setup", json={**ADMIN, "other_names": ["PEREZ FICTICIO"]}, headers=H).raise_for_status()
    assert c.get("/api/settings").json()["reader"] == "basico"
    assert c.put("/api/settings/anthropic-key", json={"key": "hola"}, headers=H).status_code == 422
    fake = "sk-ant-" + "x" * 40
    assert c.put("/api/settings/anthropic-key", json={"key": fake}, headers=H).json() == {"reader": "claude"}
    assert c.get("/api/settings").json()["reader"] == "claude"
    stored = list((tmp_path / "originals").glob("secret-*"))
    assert stored and fake.encode() not in stored[0].read_bytes()
    c.delete("/api/settings/anthropic-key", headers=H).raise_for_status()
    assert c.get("/api/settings").json()["reader"] == "basico"


def test_serves_web_app_and_catalog(client):
    assert "<title>House</title>" in client.get("/").text
    assert client.get("/app.js").status_code == 200
    cat = client.get("/api/catalog").json()
    assert cat["glucose"]["group"] == "glucosa" and cat["urine_nitrite"]["kind"] == "qual"


def test_review_pages_and_row_locations(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc_id = upload(c, me, make_pdf(LAB_LINES)).json()["document_id"]
    lay = c.get(f"/api/documents/{doc_id}/layout").json()
    assert lay["pages"] == [{"n": 1, "width": 612.0, "height": 792.0}]
    rows = {r["analyte_key"]: r["id"] for r in c.get(f"/api/documents/{doc_id}").json()["rows"]}
    box = lay["boxes"][str(rows["glucose"])]
    assert box["page"] == 1 and box["x1"] > box["x0"] and box["bottom"] > box["top"]
    png = c.get(f"/api/documents/{doc_id}/pages/1")
    assert png.headers["content-type"] == "image/png" and png.content.startswith(b"\x89PNG")
    assert c.get(f"/api/documents/{doc_id}/pages/2").status_code == 404


def test_parallel_requests_do_not_break_the_database(tmp_path):
    """Varias peticiones a la vez (como las páginas del PDF) no deben tumbar la app."""
    from concurrent.futures import ThreadPoolExecutor

    app = create_app(tmp_path, key_provider=lambda: KEY)
    db = app.state.db
    db.execute(
        "INSERT INTO person(display_name,birth_date,sex_at_birth,is_admin,pin_hash) VALUES('A','1990-01-01','M',1,?)",
        (auth.hash_pin("1234"),),
    )
    token = auth.login(db, 1, "1234")
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: auth.current_person(db, token)["id"], range(400)))
    assert results == [1] * 400


def test_reread_pending_document(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc_id = upload(c, me, make_pdf(LAB_LINES)).json()["document_id"]
    before = [r["id"] for r in c.get(f"/api/documents/{doc_id}").json()["rows"]]
    r = c.post(f"/api/documents/{doc_id}/reread", headers=H)
    assert r.status_code == 200 and r.json()["rows"] == len(before)
    after = c.get(f"/api/documents/{doc_id}").json()["rows"]
    assert {x["analyte_key"] for x in after} >= {"glucose", "urine_nitrite"}
    rows = {x["analyte_key"]: x["id"] for x in after}
    c.post(
        f"/api/documents/{doc_id}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": [{"row_id": rows["glucose"], "accept": True}]},
    )
    assert c.post(f"/api/documents/{doc_id}/reread", headers=H).status_code == 409


def test_reference_range_is_converted_with_the_value_and_old_rows_are_repaired(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lines = [
        "Fecha de Toma : 01/03/2026",
        "Proteína C Reactiva ultrasensible 0.114 < 0.5 mg/dL",
        "Glucosa 90 70 - 99 mg/dL",
    ]
    doc_id = upload(c, me, make_pdf(lines)).json()["document_id"]
    rows = {r["analyte_key"]: r for r in c.get(f"/api/documents/{doc_id}").json()["rows"]}
    crp = rows["crp_hs"]
    assert (crp["value_num"], crp["unit"], crp["ref_high"], crp["status"]) == (1.14, "mg/L", 5.0, "ok")
    decisions = [{"row_id": r["id"], "accept": True} for r in rows.values()]
    c.post(
        f"/api/documents/{doc_id}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": decisions},
    )
    obs = {o["analyte_key"]: o for o in c.get(f"/api/people/{me}/observations").json()}
    assert (obs["crp_hs"]["ref_high"], obs["crp_hs"]["status"]) == (5.0, "ok")

    # Un resultado guardado con el rango sin convertir (como antes) se repara al reiniciar.
    db = c.app.state.db
    db.execute("UPDATE observation SET ref_high = 0.5, status = NULL WHERE analyte_key = 'crp_hs'")
    from house.app import ingest

    assert ingest.repair_references(db) == 1
    row = db.execute("SELECT ref_high, status FROM observation WHERE analyte_key = 'crp_hs'").fetchone()
    assert (row["ref_high"], row["status"]) == (5.0, "ok")
    assert ingest.repair_references(db) == 0  # idempotente


def test_result_without_printed_reference_has_no_status_and_old_rows_are_repaired(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc_id = upload(
        c,
        me,
        make_pdf(["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 90 mg/dL"]),
    ).json()["document_id"]
    rows = c.get(f"/api/documents/{doc_id}").json()["rows"]
    assert rows[0]["ref_printed"] is None and rows[0]["status"] is None
    c.post(
        f"/api/documents/{doc_id}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": [{"row_id": rows[0]["id"], "accept": True}]},
    )
    db = c.app.state.db
    db.execute("UPDATE observation SET status = 'ok'")  # como lo guardaba la versión anterior
    from house.app import ingest

    assert ingest.repair_references(db) == 1
    assert db.execute("SELECT status FROM observation").fetchone()["status"] is None


# --- "Falta este resultado": indicar el análisis de una fila no reconocida y agregar a mano ---

UNKNOWN_LINES = [
    "Informe de Resultados de Laboratorio",
    "Fecha de Toma : 01/03/2026",
    "Glucosa 105 70 - 99 mg/dL",
    "Análisis Inventado XYZ 12.5 mg/dL 10 - 20",
]


def test_assign_unrecognized_row_and_add_missing_results_by_hand(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc_id = upload(c, me, make_pdf(UNKNOWN_LINES)).json()["document_id"]
    rows = c.get(f"/api/documents/{doc_id}").json()["rows"]
    unknown = next(r for r in rows if r["analyte_key"] is None)
    glucose = next(r for r in rows if r["analyte_key"] == "glucose")

    # sin decir qué análisis es, no se puede guardar
    body = {"collected_on": "2026-03-01", "decisions": [{"row_id": unknown["id"], "accept": True}]}
    assert c.post(f"/api/documents/{doc_id}/review", headers=H, json=body).status_code == 422

    body = {
        "collected_on": "2026-03-01",
        "decisions": [
            {"row_id": glucose["id"], "accept": True},
            # la persona dice que era ácido úrico: se interpreta de nuevo desde lo impreso (12.5 mg/dL, 10 - 20)
            {"row_id": unknown["id"], "accept": True, "analyte_key": "uric_acid"},
        ],
        "manual": [
            {
                "analyte_key": "crp_hs",
                "value": "0,114",
                "unit": "mg/dL",
                "ref": "< 0.5",
            },  # se convierte a mg/L
            {"analyte_key": "urine_nitrite", "value": "Negativo", "ref": "Negativo"},
            {"analyte_key": "ferritin", "value": "132"},  # sin unidad ni rango: unidad habitual, sin estado
        ],
    }
    assert c.post(f"/api/documents/{doc_id}/review", headers=H, json=body).json() == {"saved": 5}
    obs = {o["analyte_key"]: o for o in c.get(f"/api/people/{me}/observations").json()}
    assert (
        obs["uric_acid"]["value_num"],
        obs["uric_acid"]["status"],
        obs["uric_acid"]["entered_manually"],
    ) == (
        12.5,
        "ok",  # 12.5 está dentro de "10 - 20"
        0,
    )
    crp = obs["crp_hs"]
    assert (crp["value_num"], crp["unit"], crp["ref_high"], crp["status"], crp["entered_manually"]) == (
        1.14, "mg/L", 5.0, "ok", 1,
    )  # fmt: skip
    assert (obs["urine_nitrite"]["value_text"], obs["urine_nitrite"]["status"]) == ("Negativo", "ok")
    assert (obs["ferritin"]["value_num"], obs["ferritin"]["unit"], obs["ferritin"]["status"]) == (
        132,
        "ng/mL",
        None,
    )
    assert obs["glucose"]["entered_manually"] == 0


def test_manual_result_validation(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]

    def review(manual):
        doc_id = upload(c, me, make_pdf(LAB_LINES + [f"{len(manual)} {manual[0]['value']}"])).json()[
            "document_id"
        ]
        return c.post(
            f"/api/documents/{doc_id}/review",
            headers=H,
            json={"collected_on": "2026-03-01", "decisions": [], "manual": manual},
        )

    assert review([{"analyte_key": "no_existe", "value": "1"}]).status_code == 422
    assert review([{"analyte_key": "glucose", "value": "mucho"}]).status_code == 422  # numérico: exige cifra
    assert review([{"analyte_key": "glucose", "value": ""}]).status_code == 422
    r = review([{"analyte_key": "glucose", "value": "5", "unit": "furlongs"}])
    assert r.status_code == 422 and "No sé convertir" in r.json()["detail"]
    ok = review([{"analyte_key": "glucose", "value": "5.0", "unit": "mmol/L"}])
    assert ok.status_code == 200
    assert c.get(f"/api/people/{me}/observations").json()[0]["value_num"] == pytest.approx(90.08, abs=0.01)


def test_migration_adds_manual_flag_to_existing_observation_table(tmp_path):
    import sqlite3

    from house.app import ingest

    db = sqlite3.connect(tmp_path / "old.db")
    db.row_factory = sqlite3.Row
    db.executescript("CREATE TABLE observation(id INTEGER PRIMARY KEY, value_num REAL);")
    db.execute("INSERT INTO observation(value_num) VALUES (1)")
    ingest.migrate_observation(db)
    ingest.migrate_observation(db)  # idempotente
    assert db.execute("SELECT entered_manually FROM observation").fetchone()[0] == 0


def test_screen_files_are_always_revalidated_by_the_browser(client):
    for path in ("/", "/app.js", "/styles.css"):
        assert client.get(path).headers["cache-control"] == "no-cache"


def test_censored_value_survives_review_and_repair(lab_client):
    c = lab_client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lines = [
        "Informe de Resultados de Laboratorio",
        "Fecha de Toma : 01/03/2026",
        "PROCALCITONINA < 0.02 * ng/mL 0.00 - 0.50",
    ]
    doc_id = upload(c, me, make_pdf(lines)).json()["document_id"]
    row = c.get(f"/api/documents/{doc_id}").json()["rows"][0]
    assert (row["qualifier"], row["value_num"], row["status"]) == ("<", 0.02, "ok")
    c.post(
        f"/api/documents/{doc_id}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": [{"row_id": row["id"], "accept": True}]},
    )
    obs = c.get(f"/api/people/{me}/observations").json()[0]
    assert (obs["qualifier"], obs["value_num"], obs["status"]) == ("<", 0.02, "ok")
    from house.app import ingest

    assert ingest.repair_references(c.app.state.db) == 0  # sigue igual: el signo se respeta al reparar
    c.post(f"/api/documents/{doc_id}/review", headers=H, json={"collected_on": "2026-03-01", "decisions": []})


def test_review_flags_a_critical_value_so_a_misread_is_caught_before_saving(client):
    client.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = client.get("/api/me").json()["id"]
    lines = [
        "Informe de Resultados de Laboratorio",
        "Fecha de Toma : 01/03/2026",
        "Potasio 7.2 3.5 - 5.1 mmol/L",
        "Sodio 140 135 - 145 mmol/L",
    ]
    doc = upload(client, me, make_pdf(lines)).json()["document_id"]
    rows = {r["analyte_key"]: r for r in client.get(f"/api/documents/{doc}").json()["rows"]}
    assert "valor_critico" in rows["potassium"]["problems"] and rows["potassium"]["needs_attention"]
    assert "valor_critico" not in rows["sodium"]["problems"]


def test_basic_reader_goes_first_and_claude_only_steps_in_when_the_format_is_not_understood(monkeypatch):
    from types import SimpleNamespace as NS

    from house.app import ingest
    from house.providers import ProviderError

    def outcome(keys, date="2026-03-01"):
        return NS(collected_on=date, rows=[NS(key=k) for k in keys])

    calls = []

    def fake(text, router, **kw):
        calls.append(router)
        result = router()
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ingest, "extract_document", fake)
    read = lambda basic, claude: ingest.read_lab_text("t", basic, claude, ["Nombre"], None)  # noqa: E731

    good = lambda: outcome(["a", "b", "c", "d"])  # noqa: E731
    calls.clear()
    assert read(good, lambda: outcome(["a"])) == (good(), False) or calls == [
        good
    ]  # básico conforme: no se llama a Claude
    assert len(calls) == 1

    weak = lambda: outcome(["a", None, None, None])  # noqa: E731  (3 de 4 sin reconocer)
    better = lambda: outcome(["a", "b", "c", "d"])  # noqa: E731
    calls.clear()
    out, used = read(weak, better)
    assert used and len(out.rows) == 4 and len(calls) == 2

    failing = lambda: ProviderError("claude: caído")  # noqa: E731
    out, used = read(weak, failing)
    assert not used and out.rows[0].key == "a"  # si Claude falla, se conserva lo del lector básico

    worse = lambda: outcome([None])  # noqa: E731
    assert read(weak, worse)[1] is False  # Claude no leyó más: se queda el básico
    assert read(weak, None)[1] is False  # sin clave no hay respaldo
    assert (
        read(lambda: outcome(["a", "b"], date=None), better)[1] is True
    )  # sin fecha también cuenta como mal leído


def test_documents_list_says_what_was_read_but_not_saved(client):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lines = ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 105 70 - 99 mg/dL",
             "Análisis Inventado XYZ 12.5 mg/dL 10 - 20", "Sodio 140 135 - 145 mmol/L"]  # fmt: skip
    doc = upload(c, me, make_pdf(lines)).json()["document_id"]
    (item,) = c.get(f"/api/people/{me}/documents").json()
    assert [(r["printed_name"], r["lost"]) for r in item["not_saved"]] == [
        ("Análisis Inventado XYZ", True)
    ]  # pendiente

    rev = c.get(f"/api/documents/{doc}").json()
    decisions = [{"row_id": r["id"], "accept": r["analyte_key"] == "glucose"} for r in rev["rows"]]
    c.post(
        f"/api/documents/{doc}/review", json={"collected_on": "2026-03-01", "decisions": decisions}, headers=H
    ).raise_for_status()
    (item,) = c.get(f"/api/people/{me}/documents").json()
    reasons = {r["printed_name"]: r["reason"] for r in item["not_saved"]}
    assert reasons == {
        "Análisis Inventado XYZ": "No reconocí este análisis",
        "Sodio": "Decidiste no guardarlo",
    }
    assert item["results"] == 1


def test_complete_a_reviewed_study_only_adds_what_was_missing(client):
    c = client
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lines = ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 105 70 - 99 mg/dL",
             "Análisis Inventado XYZ 12.5 mg/dL 10 - 20"]  # fmt: skip
    doc = upload(c, me, make_pdf(lines)).json()["document_id"]
    rev = c.get(f"/api/documents/{doc}").json()
    decisions = [{"row_id": r["id"], "accept": r["analyte_key"] == "glucose"} for r in rev["rows"]]
    c.post(
        f"/api/documents/{doc}/review", json={"collected_on": "2026-03-01", "decisions": decisions}, headers=H
    ).raise_for_status()
    assert len(c.get(f"/api/people/{me}/observations").json()) == 1

    assert c.post(f"/api/documents/{doc}/complete-read", headers=H).json() == {"document_id": doc, "open": 1}
    rows = c.get(f"/api/documents/{doc}").json()["rows"]
    assert [(r["printed_name"], r["unsaved"]) for r in rows] == [
        ("Glucosa", False),
        ("Análisis Inventado XYZ", True),
    ]

    # lo ya guardado no se puede volver a guardar ni modificar
    glucose, other = rows
    body = {
        "collected_on": "2030-01-01",
        "complete": True,
        "decisions": [{"row_id": glucose["id"], "accept": True}],
    }
    assert c.post(f"/api/documents/{doc}/review", json=body, headers=H).status_code == 422
    body["decisions"] = [{"row_id": other["id"], "accept": True, "analyte_key": "uric_acid"}]
    assert c.post(f"/api/documents/{doc}/review", json=body, headers=H).json() == {"saved": 1}

    obs = c.get(f"/api/people/{me}/observations").json()
    assert sorted(o["analyte_key"] for o in obs) == ["glucose", "uric_acid"]
    assert {o["collected_on"] for o in obs} == {"2026-03-01"}  # la fecha del estudio no cambia
    (item,) = c.get(f"/api/people/{me}/documents").json()
    assert item["not_saved"] == [] and item["review_state"] == "revisada" and item["results"] == 2
    # un estudio pendiente no se "completa", se revisa
    doc2 = upload(c, me, make_pdf(lines + ["Sodio 140 135 - 145 mmol/L"]), "otro.pdf").json()["document_id"]
    assert c.post(f"/api/documents/{doc2}/complete-read", headers=H).status_code == 409


def test_baseline_reader_keeps_fit_and_colon_antigen_lines():
    from house.app.api import BASIC_CONFIG
    from house.extract import extract_document
    from house.providers import Router

    text = """Informe de Resultados
Fecha de Toma : 07/02/2026
EXAMEN RESULTADO UNIDADES INTERVALO DE REFERENCIA
PRUEBA INMUNOQUIMICA FECAL (FIT) 131.00 * µg Hb/g heces 0.00 - 15.00
ANTIGENO DE Cryptosporidium: NEGATIVO
ANTIGENO DE Giardia: NEGATIVO
"""
    rows = {r.key: r for r in extract_document(text, Router(BASIC_CONFIG)).rows}
    assert (rows["fit_stool"].value, rows["fit_stool"].unit, rows["fit_stool"].status) == (
        131.0,
        "µg Hb/g",
        "high",
    )
    assert (
        rows["cryptosporidium_antigen"].value_label == "NEGATIVO"
        and rows["giardia_antigen"].value_label == "NEGATIVO"
    )
