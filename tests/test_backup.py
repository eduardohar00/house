"""Respaldo cifrado: se crea, se verifica, se recupera y resiste llaves malas y archivos dañados."""

import pytest
from fastapi.testclient import TestClient

from house.app import backup
from house.app.api import create_app
from house.app.vault import Vault
from house.config import Config, ProviderConfig
from house.providers import Router
from test_app import ADMIN, KEY, H, make_pdf, upload

LAB = ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 90 70 - 99 mg/dL"]
SECRET_NAME = "Persona Ficticia Reservada"


@pytest.fixture
def world(tmp_path):
    """Una app con datos (persona, PDF cifrado, resultado confirmado) y una carpeta de respaldos."""
    data = tmp_path / "datos"
    cfg = Config(tasks={"extract": "base"}, providers={"base": ProviderConfig(name="base", kind="mock")})
    client = TestClient(create_app(data, key_provider=lambda: KEY, router=Router(cfg)))
    client.post("/api/setup", json={**ADMIN, "display_name": SECRET_NAME}, headers=H).raise_for_status()
    me = client.get("/api/me").json()["id"]
    doc = upload(client, me, make_pdf(LAB)).json()["document_id"]
    rows = client.get(f"/api/documents/{doc}").json()["rows"]
    client.post(
        f"/api/documents/{doc}/review",
        headers=H,
        json={"collected_on": "2026-03-01", "decisions": [{"row_id": rows[0]["id"], "accept": True}]},
    )
    return client, data, tmp_path / "respaldos", me


def test_recovery_key_format_and_typos():
    key, pub = backup.new_recovery_key()
    assert len(key.replace("-", "")) == 55 and pub
    backup.parse_recovery_key(key.lower().replace("-", " "))  # tolera minúsculas y espacios
    i = 12  # un carácter del medio (el último tiene bits sobrantes: cambiarlo puede no alterar la llave)
    typo = key[:i] + ("A" if key[i] != "A" else "B") + key[i + 1 :]
    with pytest.raises(backup.BackupError, match="error de captura"):
        backup.parse_recovery_key(typo)
    with pytest.raises(backup.BackupError):
        backup.parse_recovery_key("no es una llave")


def test_backup_is_encrypted_verified_and_restorable(world, tmp_path):
    client, data, dest, me = world
    key, pub = backup.new_recovery_key()
    path = backup.create_backup(data, dest, pub, KEY)
    raw = path.read_bytes()
    for secret in (SECRET_NAME.encode(), b"%PDF", b"Glucosa", b"SQLite format", KEY):
        assert secret not in raw  # nada legible: ni datos, ni PDF, ni la llave maestra

    info = backup.verify_backup(path, key)
    assert info["counts"]["person"] == 1 and info["counts"]["observation"] == 1 and info["originals"] == 1

    new_home = tmp_path / "mac-nueva"
    installed = []
    backup.restore_backup(path, key, new_home, installed.append)
    assert installed == [KEY]  # la llave maestra vuelve al llavero de la Mac nueva
    restored = TestClient(create_app(new_home, key_provider=lambda: installed[0]))
    restored.post("/api/login", json={"person_id": me, "pin": "123456"}, headers=H).raise_for_status()
    assert restored.get("/api/me").json()["display_name"] == SECRET_NAME
    obs = restored.get(f"/api/people/{me}/observations").json()
    assert obs[0]["analyte_key"] == "glucose" and obs[0]["value_num"] == 90
    doc_id = restored.get(f"/api/people/{me}/documents").json()[0]["id"]
    assert restored.get(f"/api/documents/{doc_id}/file").content == make_pdf(LAB)  # el original abre
    with pytest.raises(backup.BackupError, match="carpeta vacía"):
        backup.restore_backup(path, key, new_home, installed.append)  # nunca sobreescribe


def test_wrong_key_corruption_and_truncation_are_detected(world):
    _, data, dest, _ = world
    key, pub = backup.new_recovery_key()
    path = backup.create_backup(data, dest, pub, KEY)
    other_key, _ = backup.new_recovery_key()
    with pytest.raises(backup.BackupError, match="no corresponde"):
        backup.verify_backup(path, other_key)
    good = path.read_bytes()

    flipped = bytearray(good)
    flipped[len(good) // 2] ^= 0xFF
    path.write_bytes(bytes(flipped))
    with pytest.raises(backup.BackupError):
        backup.verify_backup(path, key)

    path.write_bytes(good[:-40])  # truncado
    with pytest.raises(backup.BackupError):
        backup.verify_backup(path, key)
    path.write_bytes(good + b"x")  # datos de más
    with pytest.raises(backup.BackupError):
        backup.verify_backup(path, key)
    path.write_bytes(b"otra cosa")
    with pytest.raises(backup.BackupError, match="no es un respaldo"):
        backup.verify_backup(path, key)


def test_large_backup_spans_several_chunks(tmp_path):
    data = tmp_path / "d"
    (data / "originals").mkdir(parents=True)
    import sqlite3

    db = sqlite3.connect(data / "house.db")
    for t in ("person", "document", "observation", "imaging_study"):
        db.execute(f"CREATE TABLE {t}(id INTEGER)")
    db.commit()
    db.close()
    vault = Vault(data / "originals", key_provider=lambda: KEY)
    big = b"\x07" * (int(backup.CHUNK * 2.5))
    vault.put(big)
    key, pub = backup.new_recovery_key()
    path = backup.create_backup(data, tmp_path / "b", pub, KEY)
    assert backup.verify_backup(path, key)["originals"] == 1
    assert path.stat().st_size > backup.CHUNK * 2.5


def test_prune_keeps_only_recent_backups(tmp_path):
    for i in range(5):
        (tmp_path / f"House-respaldo-2026-01-0{i + 1}-000000.housebak").write_bytes(b"x")
    (tmp_path / "otro.txt").write_text("no tocar")
    assert backup.prune(tmp_path, keep=2) == 3
    assert sorted(p.name for p in tmp_path.glob("*")) == [
        "House-respaldo-2026-01-04-000000.housebak",
        "House-respaldo-2026-01-05-000000.housebak",
        "otro.txt",
    ]


def test_destination_must_be_outside_data_and_writable(tmp_path):
    data = tmp_path / "datos"
    data.mkdir()
    with pytest.raises(backup.BackupError, match="fuera de los datos"):
        backup.check_destination(str(data / "respaldos"), data)
    with pytest.raises(backup.BackupError, match="ruta completa"):
        backup.check_destination("relativa/carpeta", data)
    assert backup.check_destination(str(tmp_path / "nube" / "House"), data).is_dir()


def test_api_setup_run_verify_and_permissions(world):
    client, _, dest, me = world
    assert client.get("/api/backup").json()["configured"] is False
    r = client.post("/api/backup/setup", json={"destination": str(dest)}, headers=H)
    key = r.json()["recovery_key"]
    assert r.headers["cache-control"] == "no-store" and len(key.replace("-", "")) == 55
    assert client.post("/api/backup/setup", json={"destination": str(dest)}, headers=H).status_code == 409

    st = client.get("/api/backup").json()
    assert st["configured"] and st["last_at"] is None and st["overdue"] is True and st["due"] is True
    assert "recovery_key" not in st and key not in str(st)  # House no guarda la llave

    st = client.post("/api/backup/run", headers=H).json()
    assert st["last_file"].startswith("House-respaldo-") and st["overdue"] is False and st["due"] is False
    assert (dest / st["last_file"]).exists() and st["last_size"] > 0

    ok = client.post("/api/backup/verify", json={"recovery_key": key}, headers=H).json()
    assert ok["originals"] == 1 and ok["counts"]["observation"] == 1
    bad, _ = backup.new_recovery_key()
    r = client.post("/api/backup/verify", json={"recovery_key": bad}, headers=H)
    assert r.status_code == 422 and "no corresponde" in r.json()["detail"]

    # una persona sin permisos no puede tocar el respaldo
    member = {"display_name": "Miembro", "birth_date": "1992-02-02", "sex_at_birth": "F", "pin": "2468"}
    mid = client.post("/api/people", json=member, headers=H).json()["id"]
    client.post("/api/logout", headers=H)
    client.post("/api/login", json={"person_id": mid, "pin": "2468"}, headers=H).raise_for_status()
    assert client.get("/api/backup").status_code == 403
    assert client.post("/api/backup/run", headers=H).status_code == 403

    client.post("/api/logout", headers=H)
    client.post("/api/login", json={"person_id": me, "pin": "123456"}, headers=H).raise_for_status()
    assert client.delete("/api/backup", headers=H).json()["configured"] is False
    assert list(dest.glob("*.housebak"))  # desactivar no borra los respaldos ya hechos


def test_status_flags_overdue_and_due(world):
    from datetime import datetime, timedelta

    client, _, dest, _ = world
    client.post("/api/backup/setup", json={"destination": str(dest)}, headers=H).raise_for_status()
    db = client.app.state.db
    now = datetime(2026, 3, 10, 12, 0)
    backup.put_settings(db, backup_last_at=(now - timedelta(hours=5)).isoformat())
    assert (backup.status(db, now)["due"], backup.status(db, now)["overdue"]) == (False, False)
    backup.put_settings(db, backup_last_at=(now - timedelta(hours=30)).isoformat())
    assert (backup.status(db, now)["due"], backup.status(db, now)["overdue"]) == (True, False)
    backup.put_settings(db, backup_last_at=(now - timedelta(days=9)).isoformat())
    assert backup.status(db, now)["overdue"] is True


def test_service_definition_starts_only_on_demand_and_never_at_login():
    from house.app import launchd

    d = launchd.build_plist("/x/.venv/bin/python", "/x", 8765, "/log/House.log")
    assert d["Label"] == "com.house.app" and d["ProgramArguments"][-1] == "--launchd"
    assert d["Sockets"]["Listeners"] == {
        "SockNodeName": "127.0.0.1",
        "SockServiceName": "8765",
    }  # solo esta Mac
    assert "RunAtLoad" not in d and "KeepAlive" not in d  # no arranca al iniciar sesión ni queda encendido
    assert d["StandardOutPath"] == d["StandardErrorPath"] == "/log/House.log"
