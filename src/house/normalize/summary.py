"""Resumen de un perfil: qué importa ahora, qué es una tendencia y qué ya es historia.

Reglas deterministas y auditables (la IA no decide nada aquí). Los umbrales son provisionales y
deben revisarse con un médico.

- "Vigente": el último resultado de un análisis es de los últimos RECENT_DAYS días respecto al
  estudio más reciente del perfil. Lo anterior es historial: no se reporta como alarma.
- "Atención": vigente y fuera de rango. Se ordena por qué tan lejos está, si persiste en varios
  estudios seguidos y si empeora. Lo medido va antes que lo calculado a partir de otros resultados.
- "Vigilar": vigente, dentro de rango en todo el tramo reciente, avanzando de forma sostenida
  hacia un límite. Volver de un valor alto a la normalidad no cuenta.
- "Mejoró": vigente y dentro de rango, cuando el resultado anterior estaba fuera.
- "Historial": estuvo fuera de rango más de una vez, o ya no se mide, y hoy no es motivo de alerta.
- Si un estudio no imprime rango para un análisis, se compara con el rango del estudio anterior.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from typing import Any

RECENT_DAYS = 365
STALE_STUDY_DAYS = 365
OUT = {"low", "high", "abnormal"}
PERSISTENT_STREAK = 3

# Se calculan a partir de otros resultados: si están fuera de rango casi siempre es por sus componentes.
DERIVED_KEYS = {
    "chol_hdl_ratio",
    "ldl_hdl_ratio",
    "tg_hdl_ratio",
    "ggt_ast_ratio",
    "ast_alt_ratio",
    "ag_ratio",
    "bun_creat_ratio",
    "amylase_lipase_ratio",
    "homa_ir",
    "sd_ldl",
    "non_hdl",
    "vldl",
    "total_lipids",
    "globulin",
    "bilirubin_indirect",
    "egfr",
    "egfr_cys",
    "calcium_ionized",
    "osmolality",
    "iron_sat",
    "tibc",
    "urea",
}

Obs = dict[str, Any]


def _date(o: Obs) -> date:
    return date.fromisoformat(o["collected_on"])


def _plain(s: str | None) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]+", " ", s).strip()


def classify(v: float, lo: float | None, hi: float | None) -> str:
    if lo is not None and v < lo:
        return "low"
    if hi is not None and v > hi:
        return "high"
    return "ok"


def distance_outside(o: Obs) -> float:
    """Qué tan lejos está del rango, en proporción del límite que rebasa (0 si está dentro)."""
    v, lo, hi = o["value_num"], o["ref_low"], o["ref_high"]
    if v is None:
        return 0.0
    if hi is not None and v > hi:
        return (v - hi) / (abs(hi) or 1.0)
    if lo is not None and v < lo:
        return (lo - v) / (abs(lo) or 1.0)
    return 0.0


def _margin(o: Obs) -> tuple[float, int] | None:
    """Cercanía al límite más próximo (0 = en el límite) y hacia dónde está: +1 tope, -1 piso."""
    v, lo, hi = o["value_num"], o["ref_low"], o["ref_high"]
    if v is None or (lo is None and hi is None):
        return None
    span = (hi - lo) if lo is not None and hi is not None else None
    options = []
    if hi is not None:
        options.append(((hi - v) / (span or abs(hi) or 1.0), 1))
    if lo is not None:
        options.append(((v - lo) / (span or abs(lo) or 1.0), -1))
    return min(options, key=lambda x: x[0])


def _with_reference(series: list[Obs]) -> list[Obs]:
    """Completa el rango de los resultados que no lo traen con el del estudio anterior (misma unidad)."""
    out: list[Obs] = []
    for o in series:
        if o["status"] is None and o["value_num"] is not None:
            donor = next(
                (
                    d
                    for d in reversed(out)
                    if d["unit"] == o["unit"] and (d["ref_low"] is not None or d["ref_high"] is not None)
                ),
                None,
            )
            if donor is not None:
                lo, hi = donor["ref_low"], donor["ref_high"]
                o = {
                    **o,
                    "ref_low": lo,
                    "ref_high": hi,
                    "status": classify(o["value_num"], lo, hi),
                    "ref_from": donor.get("ref_from") or donor["collected_on"],
                }
        out.append(o)
    return out


def _streak(series: list[Obs]) -> int:
    n = 0
    for o in reversed(series):
        if o["status"] in OUT:
            n += 1
        else:
            break
    return n


def _trend(series: list[Obs]) -> str | None:
    """Contra el resultado anterior: mejorando, empeorando o estable (solo numéricos)."""
    if len(series) < 2 or series[-1]["value_num"] is None or series[-2]["value_num"] is None:
        return None
    dl, dp = distance_outside(series[-1]), distance_outside(series[-2])
    if dl < dp * 0.8:
        return "mejorando"
    if dl > dp * 1.2 or dp == 0:
        return "empeorando"
    return "estable"


def _drifting_to_limit(series: list[Obs]) -> dict | None:
    """Avance sostenido hacia un límite, dentro de rango todo el tramo (no es volver a la normalidad)."""
    nums = [o for o in series if o["value_num"] is not None and o["unit"] == series[-1]["unit"]][-4:]
    if len(nums) < 3 or any(o["status"] != "ok" for o in nums):
        return None
    m = _margin(nums[-1])
    if m is None:
        return None
    margin, direction = m
    vals = [o["value_num"] for o in nums]
    steps = [b - a for a, b in zip(vals, vals[1:], strict=False)]
    toward = all(s * direction >= 0 for s in steps) and (vals[-1] - vals[0]) * direction > 0
    change = abs(vals[-1] - vals[0]) / (abs(vals[0]) or 1.0)
    if toward and margin < 0.3 and ((margin < 0.15 and change >= 0.02) or change >= 0.20):
        return {
            "first": nums[0],
            "change": change,
            "margin": margin,
            "toward": "el tope" if direction == 1 else "el piso",
        }
    return None


def _brief(o: Obs) -> Obs:
    keys = (
        "value_num",
        "qualifier",
        "value_text",
        "unit",
        "status",
        "collected_on",
        "ref_low",
        "ref_high",
        "ref_printed",
        "ref_from",
    )
    return {k: o.get(k) for k in keys}


def _derived(key: str, series: list[Obs]) -> bool:
    return key in DERIVED_KEYS or any("calcul" in _plain(o.get("method")) for o in series)


def summarize(observations: list[Obs], today: date | None = None) -> dict:
    today = today or date.today()
    raw: dict[str, list[Obs]] = {}
    for o in sorted(observations, key=lambda o: (o["collected_on"], o["analyte_key"])):
        raw.setdefault(o["analyte_key"], []).append(o)
    if not raw:
        return {
            "reference_date": None,
            "study_age_days": None,
            "study_is_old": False,
            "attention": [],
            "watch": [],
            "improved": [],
            "history": [],
            "recent_keys": [],
            "last_status": {},
            "counts": {"analytes": 0, "recent": 0, "out_now": 0, "stale": 0},
        }
    reference = max(_date(o) for o in observations)
    cutoff = reference - timedelta(days=RECENT_DAYS)
    attention, watch, improved, history = [], [], [], []
    recent_keys: list[str] = []
    last_status: dict[str, dict] = {}

    for key, original in raw.items():
        s = _with_reference(original)
        last, prev = s[-1], (s[-2] if len(s) > 1 else None)
        recent = _date(last) >= cutoff
        last_status[key] = {"status": last["status"], "ref_from": last.get("ref_from")}
        if recent:
            recent_keys.append(key)
        entry = {
            "key": key,
            "last": _brief(last),
            "previous": _brief(prev) if prev else None,
            "results": len(s),
            "derived": _derived(key, original),
        }
        if recent and last["status"] in OUT:
            streak = _streak(s)
            if streak >= PERSISTENT_STREAK:
                kind = "persistente"
            elif streak == 2:
                kind = "continua"
            else:
                kind = "nuevo" if prev is not None else "unico"
            trend = _trend(s)
            score = (
                distance_outside(last) + 0.1 * min(streak - 1, 4) + (0.1 if trend == "empeorando" else 0.0)
            )
            if last["value_num"] is None:
                score = max(score, 0.05) + 0.1 * min(streak - 1, 4)
            attention.append(
                {**entry, "streak": streak, "kind": kind, "trend": trend, "score": round(score, 3)}
            )
        elif recent and last["status"] == "ok" and prev is not None and prev["status"] in OUT:
            improved.append(entry)
        elif recent and (drift := _drifting_to_limit(s)):
            watch.append(
                {
                    **entry,
                    "first": _brief(drift["first"]),
                    "change": round(drift["change"], 3),
                    "margin": round(drift["margin"], 3),
                    "toward": drift["toward"],
                }
            )
        else:
            times_out = sum(o["status"] in OUT for o in s)
            if times_out >= 2 or (times_out >= 1 and not recent):
                history.append({**entry, "stale": not recent, "times_out": times_out})

    attention.sort(key=lambda a: (a["derived"], -a["score"]))  # primero lo medido, luego lo calculado
    return {
        "reference_date": reference.isoformat(),
        "study_age_days": (today - reference).days,
        "study_is_old": (today - reference).days > STALE_STUDY_DAYS,
        "attention": attention,
        "watch": sorted(watch, key=lambda w: w["margin"]),
        "improved": improved,
        "history": sorted(history, key=lambda h: h["last"]["collected_on"], reverse=True),
        "recent_keys": recent_keys,
        "last_status": last_status,
        "counts": {
            "analytes": len(raw),
            "recent": len(recent_keys),
            "out_now": len(attention),
            "stale": len(raw) - len(recent_keys),
        },
    }
