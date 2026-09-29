from datetime import date

from fastapi.testclient import TestClient

from house.app.api import create_app
from house.config import Config, ProviderConfig
from house.normalize import reference_ranges as rr
from house.normalize import terminology
from house.normalize.summary import apply_references, summarize
from house.providers import Router
from test_app import ADMIN, KEY, H, make_pdf, upload

_CFG = Config(tasks={"extract": "base"}, providers={"base": ProviderConfig(name="base", kind="mock")})
MAN = {"sex": "M", "birth_date": "1992-07-11"}
WOMAN = {"sex": "F", "birth_date": "1992-07-11"}
GIRL = {"sex": "F", "birth_date": "2018-01-01"}
TODAY = date(2026, 3, 1)


def obs(key, day, value, status=None, low=None, high=None, unit=None, printed=None):
    a = terminology.BY_KEY[key]
    return {
        "analyte_key": key, "collected_on": day, "value_num": value, "value_text": None, "unit": unit or a.unit,
        "status": status, "ref_low": low, "ref_high": high, "ref_printed": printed, "qualifier": None,
        "method": None, "document_id": 1, "document_title": "Estudio",
    }  # fmt: skip


def test_every_range_uses_a_real_analyte_with_sensible_limits():
    for key, rules in rr.RANGES.items():
        assert key in terminology.BY_KEY and terminology.BY_KEY[key].kind == "num", key
        for r in rules:
            assert r.low is not None or r.high is not None
            assert r.low is None or r.high is None or r.low < r.high, key
            assert r.sex in (None, "M", "F") and r.min_age >= rr.ADULT_AGE
    # las de sexo cubren a ambos; las de edad cubren todas las edades adultas sin huecos
    for key in ("hemoglobin", "hdl", "creatinine"):
        assert {r.sex for r in rr.RANGES[key]} == {"M", "F"}
    bands = sorted((r.min_age, r.max_age) for r in rr.RANGES["psa_total"])
    assert bands[0][0] == rr.ADULT_AGE and bands[-1][1] is None
    assert all(a[1] + 1 == b[0] for a, b in zip(bands, bands[1:], strict=False))


def test_general_range_depends_on_sex_and_age_and_never_applies_to_minors():
    ref, note = rr.general_ref("hemoglobin", "F", 30)
    assert (
        (ref.low, ref.high) == (12.0, 15.5) and "mujeres adultas" in note and "no es del laboratorio" in note
    )
    assert rr.general_ref("hemoglobin", "M", 30)[0].low == 13.5
    assert rr.general_ref("hdl", "F", 30)[0].low == 50 and rr.general_ref("hdl", "M", 30)[0].low == 40
    assert (
        rr.general_ref("psa_total", "M", 45)[0].high == 2.5
        and rr.general_ref("psa_total", "M", 72)[0].high == 6.5
    )
    assert rr.general_ref("psa_total", "F", 45) is None  # no aplica a mujeres
    assert "adultos" in rr.general_ref("glucose", "F", 40)[1]
    assert rr.general_ref("hemoglobin", "F", 17) is None and rr.general_ref("hemoglobin", "F", None) is None
    assert rr.general_ref("nope", "M", 30) is None
    assert rr.age_on("1992-07-11", "2026-07-10") == 33 and rr.age_on("1992-07-11", "2026-07-11") == 34
    assert rr.age_on(None, "2026-01-01") is None and rr.age_on("x", "2026-01-01") is None


def test_priority_printed_then_previous_study_then_general():
    # el rango del laboratorio manda aunque el general diría otra cosa
    printed = obs("hemoglobin", "2026-02-01", 12.8, "ok", 12.0, 16.0, printed="12 - 16")
    assert apply_references([printed], MAN)[0]["ref_source"] == "printed"
    # sin rango pero con un estudio anterior: se usa ese
    prev = obs("hemoglobin", "2025-01-01", 15.0, "ok", 12.0, 16.0)
    cur = obs("hemoglobin", "2026-02-01", 12.8)
    got = apply_references([prev, cur], MAN)[1]
    assert (got["ref_source"], got["status"], got["ref_low"]) == ("borrowed", "ok", 12.0)
    # sin nada: el general por sexo. 12.8 g/dL es normal en mujeres y bajo en hombres
    assert apply_references([cur], WOMAN)[0]["status"] == "ok"
    man = apply_references([cur], MAN)[0]
    assert (man["status"], man["ref_source"]) == ("low", "general") and "hombres adultos" in man["ref_note"]
    # sin perfil no se inventa nada
    assert apply_references([cur], None)[0]["status"] is None
    # un menor: solo cuenta lo que imprime el laboratorio
    assert apply_references([cur], GIRL)[0]["status"] is None
    # límites estrictos: el 150 de triglicéridos ya está fuera
    assert apply_references([obs("triglycerides", "2026-02-01", 150)], MAN)[0]["status"] == "high"
    assert apply_references([obs("triglycerides", "2026-02-01", 149)], MAN)[0]["status"] == "ok"
    # un valor con < o > (límite del método) no se compara con un rango general
    censored = {**obs("glucose", "2026-02-01", 5), "qualifier": "<"}
    assert apply_references([censored], MAN)[0]["status"] is None


def test_summary_uses_general_ranges_and_critical_alerts_skip_minors():
    data = [obs("hemoglobin", "2026-02-01", 12.8), obs("potassium", "2026-02-01", 7.0, "high", 3.5, 5.1)]
    s = summarize(apply_references(data, MAN), TODAY, MAN)
    assert [a["key"] for a in s["attention"]] == ["potassium", "hemoglobin"] or {
        a["key"] for a in s["attention"]
    } == {"hemoglobin", "potassium"}
    hb = next(a for a in s["attention"] if a["key"] == "hemoglobin")
    assert hb["last"]["ref_source"] == "general" and "hombres" in hb["last"]["ref_note"]
    assert [c["key"] for c in s["critical"]] == ["potassium"] and s["profile"] == {"minor": False}
    kid = summarize(apply_references(data, GIRL), TODAY, GIRL)
    assert kid["critical"] == [] and kid["profile"] == {"minor": True}
    assert [a["key"] for a in kid["attention"]] == ["potassium"]  # el general no se aplica; el impreso sí
    assert summarize(data, TODAY)["profile"] is None  # sin perfil, comportamiento anterior


def test_overview_api_fills_general_ranges_for_the_persons_sex_and_age(tmp_path):
    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(_CFG)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()  # hombre, nacido en 1990
    me = c.get("/api/me").json()["id"]
    lines = ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Hemoglobina 12.8 g/dL"]
    doc = upload(c, me, make_pdf(lines)).json()["document_id"]
    rows = c.get(f"/api/documents/{doc}").json()["rows"]
    body = {"collected_on": "2026-03-01", "decisions": [{"row_id": r["id"], "accept": True} for r in rows]}
    c.post(f"/api/documents/{doc}/review", json=body, headers=H).raise_for_status()
    out = c.get(f"/api/people/{me}/overview").json()
    (hb,) = out["observations"]
    assert (hb["status"], hb["ref_source"], hb["ref_low"], hb["ref_high"]) == ("low", "general", 13.5, 17.5)
    assert out["summary"]["attention"][0]["key"] == "hemoglobin" and out["summary"]["profile"] == {
        "minor": False
    }
    stored = c.get(f"/api/people/{me}/observations").json()[0]
    assert stored["status"] is None  # la base no se modifica: es una vista
