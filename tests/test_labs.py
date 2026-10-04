"""De qué laboratorio viene cada estudio (texto inventado)."""

from fastapi.testclient import TestClient

from house.app.api import create_app
from house.normalize.labs import detect_lab
from house.providers import Router
from test_app import ADMIN, KEY, H, make_pdf, upload
from test_reference_ranges import _CFG


def test_detect_lab_from_header_or_footer_text():
    assert detect_lab("Sucursal : HA LOMAS\nGlucosa 90") == "Ángeles Lomas"
    assert detect_lab("Hospital Ángeles Lomas\nGlucosa 90") == "Ángeles Lomas"
    assert detect_lab("Laboratorios CHOPO\nGlucosa 90") == "Chopo"
    assert detect_lab("Lapi, S.A. de C.V.\nGlucosa 90") == "Lapi"
    assert detect_lab("Un lápiz y una libreta") is None
    assert detect_lab("Glucosa 90 mg/dL") is None


def test_uploaded_study_keeps_its_lab_and_observations_carry_it(tmp_path):
    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(_CFG)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lines = ["Laboratorios Chopo", "Fecha de Toma : 01/03/2026", "Glucosa 105 70 - 99 mg/dL"]
    doc = upload(c, me, make_pdf(lines)).json()["document_id"]
    rows = c.get(f"/api/documents/{doc}").json()["rows"]
    body = {"collected_on": "2026-03-01", "decisions": [{"row_id": r["id"], "accept": True} for r in rows]}
    c.post(f"/api/documents/{doc}/review", json=body, headers=H).raise_for_status()
    (o,) = c.get(f"/api/people/{me}/overview").json()["observations"]
    assert o["lab"] == "Chopo"
    # el resultado apunta al renglón del original que lo respalda (y ese renglón tiene su caja en la página)
    assert o["row_id"] in {r["id"] for r in rows}
    boxes = c.get(f"/api/documents/{doc}/layout").json()["boxes"]
    assert str(o["row_id"]) in boxes
