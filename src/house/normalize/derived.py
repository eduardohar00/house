"""Marcadores que House calcula con fórmulas estándar cuando un estudio no los trae.

Reglas (la IA propone, el código dispone):
- Solo se calcula con resultados del MISMO día y de la misma persona, ya confirmados, en la unidad canónica.
- Solo se calcula lo que ese día no tiene: lo que imprimió el laboratorio manda siempre.
- Solo para marcadores que la persona ya tiene al menos una vez medidos por el laboratorio.
- Nada se guarda en la base: se calcula al leer, y cada valor dice de dónde sale (`calc`, `calc_text`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from . import terminology

METHOD = "Calculado por House"


def _r(x: float, digits: int = 2) -> float:
    return round(x, digits)


def _ckd_epi_2021(creatinine_mg_dl: float, age: int, sex: str) -> float:
    """CKD-EPI 2021 sin raza (Inker et al., N Engl J Med 2021): mL/min/1.73 m².

    Constantes verificadas contra la National Kidney Foundation:
    https://www.kidney.org/professionals/ckd-epi-creatinine-equation-2021 (creatinina estandarizada IDMS, mg/dL).
    """
    female = sex == "F"
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = creatinine_mg_dl / kappa
    egfr = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938**age
    return egfr * 1.012 if female else egfr


def _name(key: str) -> str:
    return terminology.BY_KEY[key].name


def _ratio(num: str, den: str) -> Callable[[dict], tuple[float, str] | None]:
    def f(v: dict) -> tuple[float, str] | None:
        if v[den]["value_num"] <= 0:
            return None
        n, d = v[num], v[den]
        return _r(
            n["value_num"] / d["value_num"]
        ), f"{_name(num)} {n['value_num']:g} ÷ {_name(den)} {d['value_num']:g}"

    return f


def _non_hdl(v: dict) -> tuple[float, str] | None:
    tc, hdl = v["chol_total"]["value_num"], v["hdl"]["value_num"]
    return _r(tc - hdl, 1), f"Colesterol total {tc:g} − HDL {hdl:g}"


def _uibc(v: dict) -> tuple[float, str] | None:
    tibc, iron = v["tibc"]["value_num"], v["iron"]["value_num"]
    return (_r(tibc - iron, 1), f"Capacidad total {tibc:g} − hierro {iron:g}") if tibc >= iron else None


def _tibc(v: dict) -> tuple[float, str] | None:
    uibc, iron = v["uibc"]["value_num"], v["iron"]["value_num"]
    return _r(uibc + iron, 1), f"Capacidad libre {uibc:g} + hierro {iron:g}"


def _ag(v: dict) -> tuple[float, str] | None:
    alb = v["albumin"]["value_num"]
    if "globulin" in v:
        glob, text = (
            v["globulin"]["value_num"],
            f"Albúmina {alb:g} ÷ globulina {v['globulin']['value_num']:g}",
        )
    elif "protein_total" in v and v["protein_total"]["value_num"] > alb:
        glob = v["protein_total"]["value_num"] - alb
        text = f"Albúmina {alb:g} ÷ (proteínas totales {v['protein_total']['value_num']:g} − albúmina)"
    else:
        return None
    return (_r(alb / glob), text) if glob > 0 else None


# clave calculada -> (entradas obligatorias, función). `_ag` acepta globulina o proteínas totales.
FORMULAS: dict[str, tuple[tuple[str, ...], Callable[[dict], tuple[float, str] | None]]] = {
    "tg_hdl_ratio": (("triglycerides", "hdl"), _ratio("triglycerides", "hdl")),
    "ldl_hdl_ratio": (("ldl", "hdl"), _ratio("ldl", "hdl")),
    "ast_alt_ratio": (("ast", "alt"), _ratio("ast", "alt")),
    "bun_creat_ratio": (("bun", "creatinine"), _ratio("bun", "creatinine")),
    "non_hdl": (("chol_total", "hdl"), _non_hdl),
    "uibc": (("tibc", "iron"), _uibc),
    "tibc": (("uibc", "iron"), _tibc),
    "ag_ratio": (("albumin",), _ag),
}


def _age(born: str, on: str) -> int | None:
    try:
        b, d = date.fromisoformat(born), date.fromisoformat(on)
    except ValueError:
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def calculate(observations: list[dict], profile: dict) -> list[dict]:
    """Resultados que faltan y se pueden calcular, con la misma forma que las observaciones guardadas."""
    have_keys = {o["analyte_key"] for o in observations}
    by_day: dict[str, dict[str, dict]] = {}
    for o in observations:
        if o.get("value_num") is None or o.get("qualifier"):
            continue
        by_day.setdefault(o["collected_on"], {}).setdefault(o["analyte_key"], o)
    out: list[dict] = []
    for day, v in sorted(by_day.items()):
        todo: list[tuple[str, float, str, list[dict]]] = []
        for key, (needs, fn) in FORMULAS.items():
            if key in v or key not in have_keys or not all(k in v for k in needs):
                continue
            if res := fn(v):
                todo.append((key, res[0], res[1], [v[k] for k in sorted(_inputs_of(key, v))]))
        age = _age(profile.get("birth_date") or "", day)
        if (
            "egfr" not in v
            and "egfr" in have_keys
            and "creatinine" in v
            and profile.get("sex") in ("M", "F")
            and age is not None
            and age >= 18
            and v["creatinine"]["value_num"] > 0
        ):
            cr = v["creatinine"]
            value = _ckd_epi_2021(cr["value_num"], age, profile["sex"])
            sexo = "mujer" if profile["sex"] == "F" else "hombre"
            text = f"Creatinina {cr['value_num']:g} mg/dL, {age} años, {sexo} (CKD-EPI 2021)"
            todo.append(("egfr", round(value), text, [cr]))
        for key, value, text, src in todo:
            a = terminology.BY_KEY[key]
            first = min(src, key=lambda s: s["document_id"])  # el estudio más antiguo de las entradas
            labs = {s.get("lab") for s in src}
            out.append(
                {
                    "analyte_key": key,
                    "printed_name": a.name,
                    "value_num": value,
                    "qualifier": None,
                    "value_text": None,
                    "unit": a.unit,
                    "ref_low": None,
                    "ref_high": None,
                    "ref_printed": None,
                    "status": None,
                    "method": METHOD,
                    "collected_on": day,
                    "document_id": first["document_id"],
                    "entered_manually": 0,
                    "document_title": first.get("document_title"),
                    "lab": labs.pop() if len(labs) == 1 else None,
                    "row_id": None,
                    "calc": True,
                    "calc_text": text,
                }
            )
    return out


def _inputs_of(key: str, v: dict) -> set[str]:
    needs = set(FORMULAS[key][0])
    if key == "ag_ratio":
        needs |= {"globulin"} if "globulin" in v else {"protein_total"}
    return needs
