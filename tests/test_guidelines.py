"""Metas de guías clínicas: aplican por edad y sexo y no cambian el estado del laboratorio."""

from datetime import date

import pytest

from house.normalize import guidelines, terminology

TODAY = date(2026, 10, 3)
MAN = {"sex": "M", "birth_date": "1992-07-11"}
WOMAN = {"sex": "F", "birth_date": "1992-07-11"}


def test_every_guideline_cites_a_source_and_has_ordered_zones():
    for key, options in guidelines.GUIDELINES.items():
        assert key in terminology.BY_KEY, key
        for g in options:
            assert g.source and g.url.startswith("https://"), key
            assert g.goal and g.short, key
            lows = [z.low for z in g.zones if z.low is not None]
            assert lows == sorted(lows), f"zonas desordenadas: {key}"
            assert g.zones[0].low is None and g.zones[-1].high is None, f"zonas sin extremos: {key}"
            for a, b in zip(g.zones, g.zones[1:], strict=False):  # sin huecos ni empalmes
                assert a.high == b.low, f"hueco entre zonas: {key}"
            assert all(z.tone in ("good", "warn", "crit", "info") for z in g.zones)


def test_total_cholesterol_goal_applies_to_adults_from_20_and_names_its_source():
    g = guidelines.for_person(MAN, TODAY)["chol_total"]
    assert g["goal"] == "menos de 200 mg/dL" and "NOM-037-SSA2-2012" in g["source"]
    assert [z["name"] for z in g["zones"]] == ["Deseable", "Riesgo (límite alto)", "Alto"]
    assert g["marks"] == [{"value": 200, "label": "< 200"}]
    assert [b["from"] for b in g["bands"]] == [200, 240]
    assert "chol_total" in guidelines.for_person(WOMAN, TODAY)


def test_no_goal_for_minors_or_without_birth_date():
    assert guidelines.for_person({"sex": "M", "birth_date": "2010-01-01"}, TODAY) == {}
    nineteen = guidelines.for_person({"sex": "M", "birth_date": "2006-10-04"}, TODAY)  # 19 años
    assert "glucose" in nineteen and "chol_total" not in nineteen  # los lípidos de la NOM, desde los 20
    assert guidelines.for_person({"sex": "M"}, TODAY) == {}


def test_sex_and_age_specific_goals():
    man, woman = guidelines.for_person(MAN, TODAY), guidelines.for_person(WOMAN, TODAY)
    assert "testosterone_total" in man and "testosterone_total" not in woman
    assert man["alt"]["marks"][0]["value"] == 29 and woman["alt"]["marks"][0]["value"] == 19
    older = guidelines.for_person({"sex": "M", "birth_date": "1980-01-01"}, TODAY)  # 46 años
    assert "testosterone_total" not in older and "alt" in older


@pytest.mark.parametrize(
    ("key", "value", "zone"),
    [
        ("chol_total", 199.9, "Deseable"),
        ("chol_total", 200, "Riesgo (límite alto)"),
        ("chol_total", 240, "Alto"),
        ("glucose", 99, "Normal"),
        ("glucose", 100, "Prediabetes"),
        ("glucose", 126, "Rango de diabetes"),
        ("hba1c", 6.4, "Prediabetes"),
        ("hba1c", 6.5, "Rango de diabetes"),
        ("hdl", 39.9, "Bajo"),
        ("hdl", 40, "Aceptable"),
        ("crp_hs", 3, "Riesgo promedio"),  # «1 a 3» incluye el 3
        ("crp_hs", 3.1, "Riesgo alto"),
        ("uacr", 300, "A2: moderadamente elevada"),
        ("uacr", 300.1, "A3: muy elevada"),
        ("egfr", 59, "G3a: leve a moderadamente disminuido"),
        ("egfr", 60, "G2: levemente disminuido"),
        ("egfr", 90, "G1: normal o alto"),
        ("ldl", 130, "Depende de tu riesgo"),
        ("testosterone_total", 9.2, "Dentro de lo esperado en hombres jóvenes sanos"),
    ],
)
def test_zone_boundaries(key, value, zone):
    g = next(o for o in guidelines.GUIDELINES[key] if o.sex in (None, "M"))
    assert guidelines.zone_of(g, value) == zone


def test_overview_carries_the_goals_without_touching_lab_status(tmp_path):
    from fastapi.testclient import TestClient

    from house.app.api import create_app
    from test_reference_ranges import _CFG, ADMIN, KEY, H, Router

    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(_CFG)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    out = c.get(f"/api/people/{c.get('/api/me').json()['id']}/overview").json()
    assert out["guidelines"]["chol_total"]["goal"] == "menos de 200 mg/dL"
    assert "ldl" in out["guidelines"] and "hemoglobin" not in out["guidelines"]
