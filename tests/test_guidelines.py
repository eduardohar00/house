"""Metas de guías clínicas: aplican por edad y sexo y no cambian el estado del laboratorio."""

from datetime import date

from house.normalize import guidelines

TODAY = date(2026, 10, 3)


def test_total_cholesterol_goal_applies_to_adults_from_20_and_names_its_source():
    g = guidelines.for_person({"sex": "M", "birth_date": "1992-07-11"}, TODAY)["chol_total"]
    assert g["goal_high"] == 200 and "NOM-037-SSA2-2012" in g["source"] and g["url"].startswith("https://")
    assert [z["name"] for z in g["zones"]] == ["Deseable", "Riesgo (límite alto)", "Alto"]
    assert guidelines.for_person({"sex": "F", "birth_date": "1992-07-11"}, TODAY).keys() == {"chol_total"}


def test_no_goal_for_minors_or_without_birth_date():
    assert guidelines.for_person({"sex": "M", "birth_date": "2010-01-01"}, TODAY) == {}
    assert guidelines.for_person({"sex": "M", "birth_date": "2006-10-04"}, TODAY) == {}  # 19 años
    assert guidelines.for_person({"sex": "M"}, TODAY) == {}


def test_zone_boundaries():
    g = guidelines.GUIDELINES["chol_total"]
    assert [guidelines.zone_of(g, v) for v in (199.9, 200, 239.9, 240)] == [
        "Deseable",
        "Riesgo (límite alto)",
        "Riesgo (límite alto)",
        "Alto",
    ]


def test_overview_carries_the_goal_without_touching_lab_status(tmp_path):
    from fastapi.testclient import TestClient

    from house.app.api import create_app
    from test_reference_ranges import _CFG, ADMIN, KEY, H, Router

    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(_CFG)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    out = c.get(f"/api/people/{c.get('/api/me').json()['id']}/overview").json()
    assert out["guidelines"]["chol_total"]["goal_high"] == 200
