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
