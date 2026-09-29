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
