"""Resumen: lo vigente se distingue del historial (datos inventados)."""

from datetime import date

from house.normalize.summary import summarize

TODAY = date(2026, 3, 1)


def obs(key, day, value, status, lo=None, hi=None, unit="mg/dL", text=None):
    return {
        "analyte_key": key,
        "collected_on": day,
        "value_num": value,
        "value_text": text,
        "unit": unit,
        "status": status,
        "ref_low": lo,
        "ref_high": hi,
        "ref_printed": None,
        "method": None,
        "document_id": 1,
        "document_title": "x",
    }


def keys(items):
    return [i["key"] for i in items]


def test_old_out_of_range_is_history_not_attention():
    data = [
        obs("uric_acid", "2018-04-03", 8.5, "high", 3.4, 7.0),  # solo se midió hace años
        obs("glucose", "2026-02-06", 90, "ok", 70, 99),
    ]
    s = summarize(data, TODAY)
    assert keys(s["attention"]) == [] and keys(s["history"]) == ["uric_acid"]
    assert s["history"][0]["stale"] is True
    assert s["counts"] == {"analytes": 2, "recent": 1, "out_now": 0, "stale": 1}
    assert "uric_acid" not in s["recent_keys"]


def test_recent_out_of_range_is_attention_ranked_by_distance_and_persistence():
    data = [
        obs("ldl", "2018-04-03", 132, "high", None, 100),
        obs("ldl", "2023-12-27", 138, "high", None, 100),
        obs("ldl", "2026-02-06", 143, "high", None, 100),  # persistente: 3 seguidos
        obs("ast", "2025-06-01", 30, "ok", None, 39),
        obs("ast", "2026-02-06", 60, "high", None, 39),  # nuevo y más lejos
        obs("mpv", "2026-02-06", 8.6, "low", 9.6, 13.4),  # apenas fuera
    ]
    a = summarize(data, TODAY)["attention"]
    assert keys(a) == ["ast", "ldl", "mpv"]
    by = {x["key"]: x for x in a}
    assert by["ldl"]["kind"] == "persistente" and by["ldl"]["streak"] == 3
    assert by["ast"]["kind"] == "nuevo" and by["ast"]["trend"] == "empeorando"


def test_recovered_and_watch():
    data = [
        obs("hdl", "2025-06-01", 35, "low", 40, 60),
        obs("hdl", "2026-02-06", 45, "ok", 40, 60),  # mejoró
        obs("glucose", "2024-06-01", 80, "ok", 70, 99),
        obs("glucose", "2025-06-01", 88, "ok", 70, 99),
        obs("glucose", "2026-02-06", 96, "ok", 70, 99),  # sube hacia el tope
        obs("alt", "2024-06-01", 20, "ok", None, 45),
        obs("alt", "2025-06-01", 22, "ok", None, 45),
        obs("alt", "2026-02-06", 21, "ok", None, 45),  # estable
    ]
    s = summarize(data, TODAY)
    assert keys(s["improved"]) == ["hdl"]
    assert keys(s["watch"]) == ["glucose"] and s["watch"][0]["toward"] == "el tope"


def test_text_result_out_of_range_is_attention_and_old_one_is_not():
    data = [
        obs("urine_crystals", "2018-04-03", None, "abnormal", text="Urato Amorfo", unit=""),
        obs("urine_appearance", "2026-02-06", None, "abnormal", text="Turbio", unit=""),
        obs("glucose", "2026-02-06", 90, "ok", 70, 99),
    ]
    s = summarize(data, TODAY)
    assert keys(s["attention"]) == ["urine_appearance"] and keys(s["history"]) == ["urine_crystals"]


def test_study_age_and_empty_profile():
    s = summarize([obs("glucose", "2023-01-10", 90, "ok", 70, 99)], TODAY)
    assert s["study_is_old"] is True and s["reference_date"] == "2023-01-10"
    empty = summarize([], TODAY)
    assert empty["attention"] == [] and empty["counts"]["analytes"] == 0


def test_missing_reference_borrows_the_previous_one_and_says_so():
    data = [
        obs("hdl", "2025-06-01", 38, "low", 40, 60),
        obs("hdl", "2026-02-06", 23.5, None),  # este estudio no imprime rango para el HDL
    ]
    s = summarize(data, TODAY)
    a = s["attention"][0]
    assert a["key"] == "hdl" and a["last"]["status"] == "low" and a["last"]["ref_from"] == "2025-06-01"
    assert s["last_status"]["hdl"] == {"status": "low", "ref_from": "2025-06-01", "ref_source": "borrowed"}
    assert s["history"] == []


def test_measured_before_calculated_and_returning_to_normal_is_not_watch():
    data = [
        obs("sd_ldl", "2026-02-06", 6.4, "high", 0, 1.35),  # calculado, muy lejos
        obs("vitamin_d", "2026-02-06", 19.5, "low", 30, 100, unit="ng/mL"),  # medido, menos lejos
        obs("ldh", "2023-12-27", 373, "high", 125, 239),
        obs("ldh", "2025-06-01", 183, "ok", 125, 239),
        obs("ldh", "2026-02-06", 148, "ok", 125, 239),
    ]
    s = summarize(data, TODAY)
    assert keys(s["attention"]) == ["vitamin_d", "sd_ldl"] and s["attention"][1]["derived"] is True
    assert s["watch"] == []  # LDH venía de un valor alto: es normalización, no acercamiento al límite


def test_history_ignores_one_off_blips_that_are_long_recovered():
    data = [
        obs("alt", "2018-04-03", 60, "high", None, 45),
        obs("alt", "2025-06-01", 20, "ok", None, 45),
        obs("alt", "2026-02-06", 21, "ok", None, 45),
    ]
    assert summarize(data, TODAY)["history"] == []


def test_borrowed_reference_points_to_the_study_that_printed_it():
    data = [
        obs("hdl", "2025-06-01", 38, "low", 40, 60),
        obs("hdl", "2026-02-06", 23.5, None),
        obs("hdl", "2026-02-08", 24.0, None),
    ]
    assert summarize(data, TODAY)["attention"][0]["last"]["ref_from"] == "2025-06-01"


def test_critical_values_alert_only_for_the_latest_study_and_never_for_censored_values():
    from house.normalize import critical

    assert critical.check("potassium", 6.9) == {"side": "high", "limit": 6.5}
    assert critical.check("hemoglobin", 6.2) == {"side": "low", "limit": 7.0}
    assert critical.check("potassium", 5.2) is None and critical.check("hdl", 5) is None
    assert critical.check("glucose", 30, "<") is None  # límite del método, no un valor medido
    assert critical.check("glucose", None) is None

    def obs(key, value, day, unit="mmol/L"):
        return {
            "analyte_key": key, "value_num": value, "value_text": None, "unit": unit, "collected_on": day,
            "ref_low": 3.5, "ref_high": 5.1, "status": "high", "qualifier": None, "method": None,
        }  # fmt: skip

    old = summarize([obs("potassium", 7.0, "2024-01-10"), obs("sodium", 140, "2026-03-01")], date(2026, 3, 5))
    assert old["critical"] == []  # el crítico es de hace 2 años: historial, no alarma
    now = summarize([obs("potassium", 7.0, "2026-03-01"), obs("sodium", 140, "2026-03-01")], date(2026, 3, 5))
    assert [c["key"] for c in now["critical"]] == ["potassium"] and now["critical"][0]["side"] == "high"


def test_only_recent_out_of_range_is_marked_important():
    data = [
        obs("ldl", "2026-02-20", 150, "high", 0, 100),  # hace ~6 meses: reciente
        obs("ferritin", "2025-06-01", 400, "high", 20, 300),  # vigente (menos de un año) pero no reciente
    ]
    s = summarize(data, TODAY)
    flags = {a["key"]: a["important"] for a in s["attention"]}
    assert flags == {"ldl": True, "ferritin": False}
