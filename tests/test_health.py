from fastapi.testclient import TestClient

from house.app import health
from house.app.api import create_app
from test_app import ADMIN, KEY, H


def world(tmp_path):
    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    other = c.post(
        "/api/people",
        json={"display_name": "Otra", "birth_date": "1995-05-05", "sex_at_birth": "F", "has_login": False},
        headers=H,
    ).json()["id"]
    return c, me, other


def test_bmi_and_ranges():
    assert health.bmi(178, 82) == 25.9 and health.bmi(None, 80) is None and health.bmi(170, None) is None


def test_profile_is_saved_validated_and_private(tmp_path):
    c, me, other = world(tmp_path)
    assert c.get(f"/api/people/{me}/health").json()["profile"] == {}
    body = {"height_cm": "178", "blood_type": "O+", "smoking": "Exfumador", "smoking_detail": "10 al día por 5 años, dejó en 2020",
            "alcohol": "Semanal", "alcohol_detail": "4 bebidas por semana", "exercise": "3 a 4 días por semana",
            "exercise_detail": "pesas y correr", "sleep_hours": "6,5", "diet": "  alta en proteína  "}  # fmt: skip
    r = c.put(f"/api/people/{me}/health/profile", json=body, headers=H)
    assert r.status_code == 200
    p = r.json()["profile"]
    assert (p["height_cm"], p["sleep_hours"], p["smoking"], p["diet"], p["blood_type"]) == (
        178.0,
        6.5,
        "Exfumador",
        "alta en proteína",
        "O+",
    )
    # se puede volver a guardar (reemplaza) y borrar un dato dejándolo vacío
    c.put(
        f"/api/people/{me}/health/profile", json={"height_cm": "180", "smoking": ""}, headers=H
    ).raise_for_status()
    p = c.get(f"/api/people/{me}/health").json()["profile"]
    assert p["height_cm"] == 180.0 and p["smoking"] is None and p["blood_type"] is None

    for bad in (
        {"height_cm": "20"},
        {"height_cm": "abc"},
        {"smoking": "A veces"},
        {"blood_type": "Z+"},
        {"diet": "x" * 501},
    ):
        assert c.put(f"/api/people/{me}/health/profile", json=bad, headers=H).status_code == 422, bad
    assert c.get(f"/api/people/{other}/health").json()["profile"] == {}  # cada persona, lo suyo


def test_measurements_validate_and_compute_bmi(tmp_path):
    c, me, other = world(tmp_path)
    c.put(f"/api/people/{me}/health/profile", json={"height_cm": "178"}, headers=H).raise_for_status()
    post = lambda **d: c.post(f"/api/people/{me}/health/measurements", json=d, headers=H)  # noqa: E731
    assert post(kind="weight_kg", value="80", measured_on="2026-01-10").status_code == 200
    w2 = post(kind="weight_kg", value="82", measured_on="2026-03-10").json()["id"]
    assert post(kind="blood_pressure", value="120", value2="80", measured_on="2026-03-10").status_code == 200
    assert post(kind="waist_cm", value="90").status_code == 200  # sin fecha: hoy
    o = c.get(f"/api/people/{me}/health").json()
    assert o["latest"]["weight_kg"]["value"] == 82 and o["bmi"] == 25.9
    assert [m["kind"] for m in o["measurements"]][-1] == "weight_kg" and o["latest"]["blood_pressure"][
        "value2"
    ] == 80

    assert post(kind="blood_pressure", value="120").status_code == 422  # falta la diastólica
    assert post(kind="blood_pressure", value="80", value2="120").status_code == 422  # invertida
    assert (
        post(kind="weight_kg", value="5").status_code == 422
        and post(kind="weight_kg", value="x").status_code == 422
    )
    assert (
        post(kind="peso", value="80").status_code == 422
        and post(kind="weight_kg", value="80", measured_on="2999-01-01").status_code == 422
    )
    assert (
        c.delete(f"/api/people/{other}/health/measurements/{w2}", headers=H).status_code == 404
    )  # no es de esa persona
    assert c.delete(f"/api/people/{me}/health/measurements/{w2}", headers=H).status_code == 200
    assert c.get(f"/api/people/{me}/health").json()["latest"]["weight_kg"]["value"] == 80
