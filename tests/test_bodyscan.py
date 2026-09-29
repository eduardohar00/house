import io

from fastapi.testclient import TestClient
from PIL import Image

from house.app import bodyscan
from house.app.api import create_app
from house.config import Config
from house.providers import LLMResponse, Router
from test_app import ADMIN, KEY, H

# Lo que trae el reporte InBody 370 de prueba (valores del reporte real, sin datos personales)
M = [  # clave, valor, normal_low, normal_high, evaluación
    ("weight_kg", 87.4, 67.4, 91.2, "normal"), ("skeletal_muscle_kg", 42.3, 34.3, 41.9, "fuerte"),
    ("body_fat_kg", 14.0, 9.5, 19.1, "normal"), ("body_fat_pct", 16.0, 10.0, 20.0, "normal"),
    ("lean_mass_kg", 69.4, None, None, None), ("fat_free_mass_kg", 73.4, None, None, None),
    ("total_body_water_l", 53.9, 44.6, 54.6, None), ("protein_kg", 14.6, 12.0, 14.6, None),
    ("mineral_kg", 4.88, 4.13, 5.05, None), ("bone_mineral_kg", 4.01, None, None, None),
    ("waist_hip_ratio", 0.93, 0.80, 0.90, "alto"), ("fitness_score", 85, None, None, None),
    ("bmr_kcal", 1956, 1814, 2135, None), ("bmi", 24.2, 18.5, 25.0, "normal"),
]  # fmt: skip
SCAN = {
    "device": "InBody 370", "measured_on": "2026-09-04", "date_printed": "4. 9. 2026", "height_cm": 189.9, "age": 34, "sex": "M",
    "metrics": [{"key": k, "value": v, "normal_low": lo, "normal_high": hi, "evaluation": ev} for k, v, lo, hi, ev in M],
    "segments": [
        {"region": "brazo_izquierdo", "lean_kg": 4.54, "lean_evaluation": "alto", "fat_kg": 0.5, "fat_pct": 9.3, "fat_evaluation": "bajo"},
        {"region": "tronco", "lean_kg": 33.7, "lean_evaluation": "normal", "fat_kg": 7.8, "fat_pct": 18.0, "fat_evaluation": "normal"},
    ],
    "notes": "Control de peso -1.0 kg; control de grasa -1.0 kg; control de músculo 0.0 kg",
}  # fmt: skip


class FakeScan:
    name = "fake"

    def __init__(self, data=SCAN):
        self.data, self.requests = data, []

    def complete_json(self, req):
        self.requests.append(req)
        return LLMResponse(data=self.data, provider="fake", model="m")


def make(tmp_path, fake):
    cfg = Config(tasks={"extract": "fake", "interpret": "fake"}, providers={})
    return TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"fake": fake}))
    )


def png():
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, "PNG")
    return buf.getvalue()


def upload(c, pid, data=None, name="Inbody.png"):
    return c.post(
        f"/api/people/{pid}/body-scans", files={"file": (name, data or png(), "image/png")}, headers=H
    )


def test_clean_and_consistency_checks():
    out = bodyscan.clean(SCAN)
    assert out["warnings"] == [] and out["measured_on"] == "2026-09-04" and len(out["metrics"]) == 14
    bad = {**SCAN, "measured_on": "4.9.2026", "metrics": [{"key": "weight_kg", "value": 80, "normal_low": None, "normal_high": None, "evaluation": None},
           {"key": "body_fat_kg", "value": 30, "normal_low": None, "normal_high": None, "evaluation": None},
           {"key": "body_fat_pct", "value": 10, "normal_low": None, "normal_high": None, "evaluation": None},
           {"key": "skeletal_muscle_kg", "value": 900, "normal_low": None, "normal_high": None, "evaluation": "genial"},
           {"key": "cosa", "value": 1, "normal_low": None, "normal_high": None, "evaluation": None}]}  # fmt: skip
    w = bodyscan.clean(bad)
    assert w["measured_on"] is None and any("fecha" in x for x in w["warnings"])
    assert any("no cuadran" in x for x in w["warnings"]) and any(
        "fuera de lo posible" in x for x in w["warnings"]
    )
    assert [m["key"] for m in w["metrics"]] == [
        "weight_kg",
        "body_fat_kg",
        "body_fat_pct",
        "skeletal_muscle_kg",
    ]  # sin claves raras
    assert [m["evaluation"] for m in w["metrics"]][-1] is None  # evaluación inventada: se descarta


def test_upload_review_confirm_updates_measurements_height_and_shows_in_history(tmp_path):
    fake = FakeScan()
    c = make(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    c.put(f"/api/people/{me}/health/profile", json={"height_cm": "178"}, headers=H).raise_for_status()
    c.post(
        f"/api/people/{me}/health/measurements",
        json={"kind": "weight_kg", "value": "80", "measured_on": "2026-01-01"},
        headers=H,
    )

    r = upload(c, me)
    assert r.status_code == 200 and r.json()["metrics"] == 14
    (req,) = fake.requests
    assert "NO transcribas datos personales" in req.system and len(req.images) == 1
    assert upload(c, me).status_code == 409  # el mismo reporte otra vez
    doc = r.json()["document_id"]

    rev = c.get(f"/api/documents/{doc}").json()
    assert rev["document"]["doc_type"] == "otro" and rev["body_scan"]["measured_on"] == "2026-09-04"
    assert rev["current_height_cm"] == 178.0 and rev["body_scan"]["height_cm"] == 189.9
    assert c.get(f"/api/people/{me}/health").json()["body_scans"] == []  # nada entra sin confirmar

    assert c.post(f"/api/documents/{doc}/review-body-scan", json={"keys": []}, headers=H).status_code == 422
    assert (
        c.post(
            f"/api/documents/{doc}/review-body-scan",
            json={"keys": ["weight_kg"], "measured_on": "hoy"},
            headers=H,
        ).status_code
        == 422
    )
    body = {"measured_on": "2026-09-04", "keys": ["weight_kg", "skeletal_muscle_kg", "body_fat_pct", "waist_hip_ratio", "bmr_kcal", "bmi"],
            "update_height": True, "height_cm": 189.9}  # fmt: skip
    assert c.post(f"/api/documents/{doc}/review-body-scan", json=body, headers=H).json() == {
        "saved": 5
    }  # el IMC del reporte solo se conserva en el reporte
    assert c.post(f"/api/documents/{doc}/review-body-scan", json=body, headers=H).status_code == 409

    h = c.get(f"/api/people/{me}/health").json()
    assert h["profile"]["height_cm"] == 189.9
    latest = {k: v["value"] for k, v in h["latest"].items()}
    assert (
        latest["weight_kg"] == 87.4
        and latest["skeletal_muscle_kg"] == 42.3
        and latest["whr"] == 0.93
        and latest["bmr_kcal"] == 1956
    )
    assert h["bmi"] == 24.2  # calculado con la talla y el peso nuevos
    assert [m["value"] for m in h["measurements"] if m["kind"] == "weight_kg"] == [
        87.4,
        80,
    ]  # el peso anterior se conserva
    (scan,) = h["body_scans"]
    assert (
        scan["document_id"] == doc
        and {m["key"]: m["evaluation"] for m in scan["metrics"]}["waist_hip_ratio"] == "alto"
    )

    (item,) = [d for d in c.get(f"/api/people/{me}/documents").json() if d["id"] == doc]
    assert (
        item["review_state"] == "revisada"
        and item["results"] == 5
        and item["title"] == "Composición corporal"
    )
    assert item["filename"] == "Inbody.png"
    ev = [e for e in c.get(f"/api/people/{me}/clinical").json()["timeline"] if e["kind"] == "cuerpo"]
    assert ev and ev[0]["date"] == "2026-09-04"


def test_body_scan_needs_claude_and_rejects_empty_reads(tmp_path):
    plain = TestClient(create_app(tmp_path / "a", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    r = upload(plain, plain.get("/api/me").json()["id"])
    assert r.status_code == 409 and "Claude" in r.json()["detail"]
    c = make(tmp_path / "b", FakeScan({**SCAN, "metrics": []}))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    assert upload(c, me).status_code == 422 and c.get(f"/api/people/{me}/documents").json() == []


def test_old_measurement_tables_are_upgraded_and_assistant_sees_the_scan(tmp_path):
    import sqlite3

    from house.app import health

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE person(id INTEGER PRIMARY KEY);"
        "CREATE TABLE measurement(id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL, kind TEXT NOT NULL "
        "CHECK (kind IN ('weight_kg','blood_pressure','heart_rate','waist_cm','body_fat_pct')), value REAL NOT NULL, "
        "value2 REAL, measured_on TEXT NOT NULL, notes TEXT);"
        "INSERT INTO measurement(person_id, kind, value, measured_on) VALUES (1, 'weight_kg', 80, '2026-01-01');"
    )
    health.migrate(db)
    assert [tuple(r) for r in db.execute("SELECT kind, value FROM measurement")] == [("weight_kg", 80.0)]
    db.execute(
        "INSERT INTO measurement(person_id, kind, value, measured_on, document_id) VALUES (1, 'whr', 0.9, '2026-02-01', 3)"
    )

    fake = FakeScan()
    c = make(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc = upload(c, me).json()["document_id"]
    c.post(
        f"/api/documents/{doc}/review-body-scan",
        json={"measured_on": "2026-09-04", "keys": ["weight_kg"]},
        headers=H,
    ).raise_for_status()
    person = sqlite3.connect(tmp_path / "house.db")
    person.row_factory = sqlite3.Row
    from datetime import date

    from house.app import assistant

    row = person.execute("SELECT * FROM person WHERE id = ?", (me,)).fetchone()
    box = assistant.Toolbox(person, row, [], date(2026, 9, 29))
    prof = box.get_health_profile()
    assert prof["body_composition_reports"][0]["metrics"][0]["name"] == "Peso"
    assert "composición corporal (2026-09-04)" in box.person_context()
    assert "masa muscular esquelética 42.3 kg" in box.person_context()


def _walk(node, path="$"):
    """Recorre un esquema JSON y devuelve los problemas que la API de Anthropic rechaza."""
    problems = []
    if isinstance(node, dict):
        if "enum" in node:
            types = node.get("type", [])
            types = [types] if isinstance(types, str) else types
            check = {
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
                "null": type(None),
            }
            for v in node["enum"]:
                if types and not any(isinstance(v, check[t]) for t in types):
                    problems.append(f"{path}: el valor {v!r} no coincide con el tipo {types}")
        if node.get("type") == "object":
            props = node.get("properties", {})
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: falta additionalProperties=false")
            if set(node.get("required", [])) != set(props):
                problems.append(f"{path}: todos los campos deben ser requeridos")
        for k, v in node.items():
            problems += _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            problems += _walk(v, f"{path}[{i}]")
    return problems


def test_every_json_schema_sent_to_claude_is_valid_for_structured_outputs():
    """Un esquema mal armado hace que Anthropic conteste 400 (BadRequestError): se revisan todos aquí."""
    from house.app import drugs, naming, prescriptions, tables
    from house.schema import extraction_json_schema

    for name, schema in {
        "composición": bodyscan.scan_schema(), "recetas": prescriptions.prescription_schema(),
        "tablas": tables.tables_schema(), "nombres": naming.schema(), "sustancias": drugs.schema(),
        "laboratorio": extraction_json_schema(),
    }.items():  # fmt: skip
        assert _walk(schema) == [], name
