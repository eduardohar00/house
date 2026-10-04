"""Marcadores calculados: solo con resultados del mismo día, sin pisar lo que imprimió el laboratorio."""

import pytest

from house.normalize import derived

MAN = {"sex": "M", "birth_date": "1992-07-11"}


def ob(key, day, value, doc=1, lab="Chopo", **kw):
    return {
        "analyte_key": key,
        "collected_on": day,
        "value_num": value,
        "qualifier": None,
        "document_id": doc,
        "document_title": f"Estudio {doc}",
        "lab": lab,
        **kw,
    }


def test_ratio_is_calculated_only_where_the_study_does_not_bring_it():
    real = [
        ob("triglycerides", "2024-01-01", 150, 1),
        ob("hdl", "2024-01-01", 50, 1),
        ob("triglycerides", "2025-01-01", 140, 2),
        ob("hdl", "2025-01-01", 40, 2),
        ob("tg_hdl_ratio", "2025-01-01", 3.4, 2),  # este día lo imprimió el laboratorio: no se toca
    ]
    (c,) = derived.calculate(real, MAN)
    assert (c["analyte_key"], c["collected_on"], c["value_num"]) == ("tg_hdl_ratio", "2024-01-01", 3.0)
    assert c["calc"] is True and c["method"] == "Calculado por House" and c["row_id"] is None
    assert c["calc_text"] == "Triglicéridos 150 ÷ Colesterol HDL 50" and c["lab"] == "Chopo"


def test_only_for_markers_the_person_already_has_measured_by_a_lab():
    real = [ob("triglycerides", "2024-01-01", 150), ob("hdl", "2024-01-01", 50)]
    assert derived.calculate(real, MAN) == []  # nunca se midió la relación: no se inventa el marcador


def test_inputs_must_be_same_day_and_not_censored():
    real = [
        ob("triglycerides", "2024-01-01", 150),
        ob("hdl", "2024-01-02", 50),  # otro día
        ob("tg_hdl_ratio", "2020-01-01", 2.0),
        ob("ast", "2024-01-01", 30, qualifier="<"),
        ob("alt", "2024-01-01", 30),
        ob("ast_alt_ratio", "2020-01-01", 1.0),
    ]
    assert derived.calculate(real, MAN) == []


def test_non_hdl_uibc_and_albumin_globulin_ratio():
    real = [
        ob("chol_total", "2026-10-03", 217),
        ob("hdl", "2026-10-03", 36),
        ob("non_hdl", "2026-02-06", 150),
        ob("tibc", "2026-10-03", 148),
        ob("iron", "2026-10-03", 78.6),
        ob("uibc", "2026-02-06", 100),
        ob("albumin", "2026-10-03", 4.8),
        ob("protein_total", "2026-10-03", 7.6),
        ob("ag_ratio", "2026-02-06", 1.5),
    ]
    got = {c["analyte_key"]: c["value_num"] for c in derived.calculate(real, MAN)}
    assert got == {"non_hdl": 181.0, "uibc": 69.4, "ag_ratio": 1.71}


def test_total_iron_binding_capacity_is_free_capacity_plus_iron():
    real = [
        ob("uibc", "2026-10-03", 148),
        ob("iron", "2026-10-03", 166),
        ob("tibc", "2026-02-06", 312.2),
    ]
    (c,) = derived.calculate(real, MAN)
    assert (c["analyte_key"], c["value_num"]) == ("tibc", 314.0)


def test_egfr_ckd_epi_2021_uses_age_on_the_study_date_and_sex():
    real = [ob("creatinine", "2026-10-03", 1.14), ob("egfr", "2020-01-01", 90)]
    (c,) = derived.calculate(real, MAN)  # 34 años en esa fecha
    assert c["analyte_key"] == "egfr" and c["value_num"] == 87 and "34 años" in c["calc_text"]
    (w,) = derived.calculate(
        [ob("creatinine", "2026-10-03", 0.8), ob("egfr", "2020-01-01", 90)], {**MAN, "sex": "F"}
    )
    assert w["value_num"] == 99 and "mujer" in w["calc_text"]  # 34 años, mujer
    assert derived.calculate(real, {"sex": "M"}) == []  # sin fecha de nacimiento no se calcula
    assert derived.calculate(real, {"birth_date": "1992-07-11"}) == []  # sin sexo tampoco
    assert derived.calculate(real, {"sex": "M", "birth_date": "2015-01-01"}) == []  # menores: no


@pytest.mark.parametrize(
    ("cr", "age", "sex", "expected"), [(1.14, 34, "M", 86.5), (0.8, 60, "F", 84.3), (0.9, 40, "M", 110.7)]
)
def test_ckd_epi_reference_values(cr, age, sex, expected):
    assert round(derived._ckd_epi_2021(cr, age, sex), 1) == expected


def test_overview_adds_calculated_points_without_touching_the_summary(tmp_path):
    from fastapi.testclient import TestClient

    from house.app.api import create_app
    from test_app import ADMIN, KEY, H, make_pdf, upload
    from test_reference_ranges import _CFG, Router

    c = TestClient(create_app(tmp_path, key_provider=lambda: KEY, router=Router(_CFG)))
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    for day, lines in (
        ("01/03/2025", ["Triglicéridos 150 0 - 149 mg/dL", "Colesterol HDL 50 40 - 60 mg/dL"]),
        (
            "01/03/2026",
            [
                "Triglicéridos 140 0 - 149 mg/dL",
                "Colesterol HDL 40 40 - 60 mg/dL",
                "Relación triglicéridos/HDL 3.5 0 - 3",
            ],
        ),
    ):
        pdf = make_pdf(["Informe de Resultados de Laboratorio", f"Fecha de Toma : {day}", *lines])
        doc = upload(c, me, pdf, f"{day[-4:]}.pdf").json()["document_id"]
        rows = c.get(f"/api/documents/{doc}").json()["rows"]
        iso = f"{day[6:]}-{day[3:5]}-{day[:2]}"
        body = {
            "collected_on": iso,
            "decisions": [{"row_id": r["id"], "accept": bool(r["analyte_key"])} for r in rows],
        }
        c.post(f"/api/documents/{doc}/review", json=body, headers=H).raise_for_status()
    out = c.get(f"/api/people/{me}/overview").json()
    calc = [o for o in out["observations"] if o.get("calc")]
    assert [(o["analyte_key"], o["collected_on"], o["value_num"]) for o in calc] == [
        ("tg_hdl_ratio", "2025-03-01", 3.0)
    ]
    assert all(not o.get("calc") for o in out["observations"][: -len(calc)])  # lo calculado va al final
    stored = c.get(f"/api/people/{me}/observations").json()
    assert not any(o["method"] == "Calculado por House" for o in stored)  # nada calculado se guarda
