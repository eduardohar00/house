"""Metas y cortes de guías clínicas por marcador (distintos del rango de referencia del laboratorio).

El rango del laboratorio dice qué es habitual en personas sanas con ese método; una guía clínica define cortes
de riesgo o de diagnóstico, que a veces son más exigentes. House los muestra como una segunda marca en la
gráfica y NUNCA cambia con ellos el estado («Atención/Vigilar») ni los rangos del laboratorio.

Solo se incluyen marcadores con un corte publicado por una guía u organismo citable, que se verificó contra
su texto o resumen oficial. Quedan fuera a propósito los que no tienen un corte confiable para esta persona:
hemoglobina (el corte de la OMS cambia con la altitud y esta persona vive a más de 2,000 m), vitamina D (la
Endocrine Society retiró sus cortes en 2024) y vitamina B12 (la guía británica dice que no hay un corte
definitivo y que se usen rangos locales).

PROVISIONAL: cada meta cita su fuente y debe confirmarse con un médico. Los límites están en la unidad
canónica del catálogo.

Convención de las zonas (de menor a mayor valor): `low` se incluye y `high` se excluye, salvo que `low_excl` /
`high_incl` digan lo contrario. `tone`: good (cumple), warn (por encima o por debajo del corte), crit (muy
alterado) o info (depende del riesgo de la persona, que House no calcula).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import terminology


@dataclass(frozen=True)
class Zone:
    name: str
    text: str  # el rango tal como se muestra («200 a 239»)
    low: float | None = None
    high: float | None = None
    tone: str = "good"
    low_excl: bool = False
    high_incl: bool = False


@dataclass(frozen=True)
class Guideline:
    key: str
    zones: tuple[Zone, ...]
    goal: str  # frase de la meta; «{u}» se reemplaza por la unidad del marcador
    short: str  # versión corta para la leyenda y la pregunta al asistente
    source: str
    url: str
    min_age: int = 18
    max_age: int | None = None
    sex: str | None = None  # None = igual para ambos sexos
    notes: tuple[str, ...] = field(default_factory=tuple)


_NOM = "NOM-037-SSA2-2012 (dislipidemias, México)"
_NOM_URL = "https://www.scielo.org.mx/scielo.php?script=sci_arttext&pid=S0188-21982012000300001"
_ADA = "ADA, Standards of Care in Diabetes—2025 (sección 2)"
_ADA_URL = "https://diabetesjournals.org/care/issue/48/Supplement_1"
_KDIGO = "KDIGO 2024, guía de enfermedad renal crónica"
_KDIGO_URL = "https://kdigo.org/wp-content/uploads/2024/03/KDIGO-2024-CKD-Guideline.pdf"

_RISK_NOTE = (
    "La NOM-037 fija la meta según el nivel de riesgo cardiovascular: riesgo alto, menos de {hi}; "
    "intermedio, "
    "menos de {mid}; bajo, menos de {lo}. House no calcula tu riesgo: tu médico lo define."
)

_EGFR_ZONES = (
    Zone("G5: falla renal", "menos de 15", None, 15, "crit"),
    Zone("G4: gravemente disminuido", "15 a 29", 15, 30, "crit"),
    Zone("G3b: moderada a gravemente disminuido", "30 a 44", 30, 45, "warn"),
    Zone("G3a: leve a moderadamente disminuido", "45 a 59", 45, 60, "warn"),
    Zone("G2: levemente disminuido", "60 a 89", 60, 90, "good"),
    Zone("G1: normal o alto", "90 o más", 90, None, "good"),
)
_EGFR_NOTES = (
    "Un solo valor no diagnostica enfermedad renal: KDIGO la define con un filtrado menor de 60 "
    "durante más de "
    "3 meses, o con daño renal (por ejemplo albúmina en orina).",
)

GUIDELINES: dict[str, tuple[Guideline, ...]] = {
    "chol_total": (
        Guideline(
            "chol_total",
            (
                Zone("Deseable", "menos de 200", None, 200, "good"),
                Zone("Riesgo (límite alto)", "200 a 239", 200, 240, "warn"),
                Zone("Alto", "240 o más", 240, None, "crit"),
            ),
            "menos de 200 {u}",
            "menos de 200",
            _NOM,
            _NOM_URL,
            min_age=20,
            notes=(
                "Son los mismos cortes que NCEP ATP III (EE. UU.) y valen igual para hombres y "
                "mujeres adultos.",
                "Las guías europeas y estadounidenses actuales basan las metas en el colesterol LDL "
                "y no-HDL, "
                "según tu riesgo cardiovascular; el colesterol total solo es una referencia general.",
            ),
        ),
    ),
    "ldl": (
        Guideline(
            "ldl",
            (
                Zone("Cumple la meta de riesgo alto", "menos de 100", None, 100, "good"),
                Zone("Depende de tu riesgo", "100 a 159", 100, 160, "info"),
                Zone("Por encima de la meta de cualquier riesgo", "160 o más", 160, None, "warn"),
            ),
            "depende de tu riesgo: menos de 100 {u} (riesgo alto), 130 (intermedio) o 160 (bajo)",
            "menos de 100 si el riesgo es alto (130 intermedio, 160 bajo)",
            _NOM,
            _NOM_URL,
            min_age=20,
            notes=(
                _RISK_NOTE.format(hi=100, mid=130, lo=160)
                + " Con enfermedad cardiovascular establecida o diabetes, la NOM propone menos de 70.",
            ),
        ),
    ),
    "non_hdl": (
        Guideline(
            "non_hdl",
            (
                Zone("Cumple la meta de riesgo alto", "menos de 130", None, 130, "good"),
                Zone("Depende de tu riesgo", "130 a 189", 130, 190, "info"),
                Zone("Por encima de la meta de cualquier riesgo", "190 o más", 190, None, "warn"),
            ),
            "depende de tu riesgo: menos de 130 {u} (riesgo alto), 160 (intermedio) o 190 (bajo)",
            "menos de 130 si el riesgo es alto (160 intermedio, 190 bajo)",
            _NOM,
            _NOM_URL,
            min_age=20,
            notes=(
                _RISK_NOTE.format(hi=130, mid=160, lo=190),
                "En una sola toma de detección, la NOM marca como caso probable de dislipidemia un no-HDL de "
                "160 o más.",
            ),
        ),
    ),
    "hdl": (
        Guideline(
            "hdl",
            (
                Zone("Bajo", "menos de 40", None, 40, "warn"),
                Zone("Aceptable", "40 o más", 40, None, "good"),
            ),
            "40 {u} o más",
            "40 o más",
            _NOM,
            _NOM_URL,
            min_age=20,
            notes=("La NOM usa el mismo corte para hombres y mujeres.",),
        ),
    ),
    "triglycerides": (
        Guideline(
            "triglycerides",
            (
                Zone("Normal", "menos de 150", None, 150, "good"),
                Zone("Elevado", "150 o más", 150, None, "warn"),
            ),
            "menos de 150 {u}",
            "menos de 150",
            _NOM,
            _NOM_URL,
            min_age=20,
            notes=("En una sola toma de detección, 150 o más marca un caso probable de dislipidemia.",),
        ),
    ),
    "glucose": (
        Guideline(
            "glucose",
            (
                Zone("Normal", "menos de 100", None, 100, "good"),
                Zone("Prediabetes", "100 a 125", 100, 126, "warn"),
                Zone("Rango de diabetes", "126 o más", 126, None, "crit"),
            ),
            "menos de 100 {u} en ayunas",
            "menos de 100 en ayunas",
            _ADA,
            _ADA_URL,
            notes=(
                "Aplica a la glucosa en ayunas. Un solo resultado no diagnostica: tu médico lo confirma.",
                "La guía define los cortes hacia arriba; los valores bajos los valora el laboratorio.",
            ),
        ),
    ),
    "hba1c": (
        Guideline(
            "hba1c",
            (
                Zone("Normal", "menos de 5.7", None, 5.7, "good"),
                Zone("Prediabetes", "5.7 a 6.4", 5.7, 6.5, "warn"),
                Zone("Rango de diabetes", "6.5 o más", 6.5, None, "crit"),
            ),
            "menos de 5.7 {u}",
            "menos de 5.7",
            _ADA,
            _ADA_URL,
            notes=("Un solo resultado no diagnostica: tu médico lo confirma.",),
        ),
    ),
    "crp_hs": (
        Guideline(
            "crp_hs",
            (
                Zone("Riesgo bajo", "menos de 1", None, 1, "good"),
                Zone("Riesgo promedio", "1 a 3", 1, 3, "info", high_incl=True),
                Zone("Riesgo alto", "más de 3", 3, None, "warn", low_excl=True),
            ),
            "menos de 1 {u}",
            "menos de 1",
            "AHA/CDC, declaración científica sobre marcadores de inflamación (Pearson 2003)",
            "https://www.ahajournals.org/doi/10.1161/01.CIR.0000052939.59093.45",
            notes=(
                "Esta clasificación valora el riesgo cardiovascular a largo plazo. Un valor de 10 o "
                "más suele "
                "reflejar una inflamación aguda (infección, lesión) y la guía no recomienda usarlo para ese "
                "fin: se repite cuando la persona está bien.",
            ),
        ),
    ),
    "egfr": (
        Guideline("egfr", _EGFR_ZONES, "60 {u} o más", "60 o más", _KDIGO, _KDIGO_URL, notes=_EGFR_NOTES),
    ),
    "egfr_cys": (
        Guideline("egfr_cys", _EGFR_ZONES, "60 {u} o más", "60 o más", _KDIGO, _KDIGO_URL, notes=_EGFR_NOTES),
    ),
    "uacr": (
        Guideline(
            "uacr",
            (
                Zone("A1: normal o ligeramente elevada", "menos de 30", None, 30, "good"),
                Zone("A2: moderadamente elevada", "30 a 300", 30, 300, "warn", high_incl=True),
                Zone("A3: muy elevada", "más de 300", 300, None, "crit", low_excl=True),
            ),
            "menos de 30 {u}",
            "menos de 30",
            _KDIGO,
            _KDIGO_URL,
            notes=(
                "KDIGO pide confirmar una albuminuria elevada con otra muestra antes de darle importancia.",
            ),
        ),
    ),
    "alt": tuple(
        Guideline(
            "alt",
            (
                Zone("Dentro del límite saludable", f"menos de {lo}", None, lo, "good"),
                Zone(f"En el límite ({lo} a {hi})", f"{lo} a {hi}", lo, hi + 1, "warn"),
                Zone("Por encima del límite", f"más de {hi}", hi + 1, None, "warn"),
            ),
            f"menos de {lo} {{u}}",
            f"menos de {lo}",
            "ACG, evaluación de pruebas hepáticas anormales (2017)",
            ""
            "https://journals.lww.com/ajg/fulltext/2017/01000/acg_clinical_guideline__evaluation_of_abnormal.13.aspx",
            sex=sex,
            notes=(
                f"El ACG propone un límite superior saludable de {lo} a {hi} para "
                f"{'hombres' if sex == 'M' else 'mujeres'}, basado en personas sin factores de "
                f"riesgo de hígado. "
                "Los límites que imprimen los laboratorios suelen ser más altos.",
            ),
        )
        for sex, lo, hi in (("M", 29, 33), ("F", 19, 25))
    ),
    "testosterone_total": (
        Guideline(
            "testosterone_total",
            (
                Zone("Por debajo del límite de hombres jóvenes sanos", "menos de 9.2", None, 9.2, "warn"),
                Zone("Dentro de lo esperado en hombres jóvenes sanos", "9.2 o más", 9.2, None, "good"),
            ),
            "9.2 {u} o más (264 ng/dL)",
            "9.2 o más (264 ng/dL)",
            "Endocrine Society, testosterona en hombres con hipogonadismo (2018)",
            "https://academic.oup.com/jcem/article/103/5/1715/4939465",
            min_age=19,
            max_age=39,
            sex="M",
            notes=(
                "Es el límite inferior armonizado de hombres jóvenes sanos (264 ng/dL, 9.2 nmol/L). La guía "
                "diagnostica hipogonadismo solo con síntomas y con testosterona baja en dos mañanas "
                "distintas, "
                "en ayunas.",
            ),
        ),
    ),
    "ferritin": (
        Guideline(
            "ferritin",
            (
                Zone("Por debajo del corte de deficiencia de hierro", "menos de 15", None, 15, "warn"),
                Zone("Sin deficiencia de hierro", "15 o más", 15, None, "good"),
            ),
            "15 {u} o más",
            "15 o más",
            "OMS, uso de la ferritina para valorar el hierro (2020)",
            "https://www.who.int/publications/i/item/9789240000124",
            notes=(
                "Es el corte para adultos sanos. Con infección o inflamación la ferritina sube y la OMS usa "
                "otro corte, así que un valor normal no descarta deficiencia en ese caso.",
            ),
        ),
    ),
}


def _in_zone(z: Zone, v: float) -> bool:
    above = z.low is None or (v > z.low if z.low_excl else v >= z.low)
    below = z.high is None or (v <= z.high if z.high_incl else v < z.high)
    return above and below


def zone_of(g: Guideline, value: float) -> str:
    for z in g.zones:
        if _in_zone(z, value):
            return z.name
    return ""


def _marks(g: Guideline) -> list[dict]:
    """Líneas de la gráfica: donde la guía pasa de «cumple» a otra cosa."""
    out = []
    for a, b in zip(g.zones, g.zones[1:], strict=False):
        if (a.tone == "good") != (b.tone == "good"):
            v = a.high if a.high is not None else b.low
            out.append({"value": v, "label": f"< {v:g}" if a.tone == "good" else f"≥ {v:g}"})
    return out


def _bands(g: Guideline) -> list[dict]:
    """Franjas a sombrear: las zonas que no cumplen (aviso o alteradas); «depende del riesgo» no se "
    "sombrea."""
    return [{"from": z.low, "to": z.high, "tone": z.tone} for z in g.zones if z.tone in ("warn", "crit")]


def for_person(profile: dict, today: date | None = None) -> dict[str, dict]:
    """Metas que aplican a esta persona (por edad y sexo), listas para la pantalla."""
    today = today or date.today()
    try:
        born = date.fromisoformat(profile.get("birth_date") or "")
    except ValueError:
        return {}
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    out: dict[str, dict] = {}
    for key, options in GUIDELINES.items():
        g = next(
            (
                o
                for o in options
                if age >= o.min_age
                and (o.max_age is None or age <= o.max_age)
                and (o.sex is None or o.sex == profile.get("sex"))
            ),
            None,
        )
        if g is None:
            continue
        unit = terminology.BY_KEY[key].unit if key in terminology.BY_KEY else ""
        out[key] = {
            "goal": g.goal.replace("{u}", unit).strip(),
            "short": g.short,
            "zones": [
                {
                    "name": z.name,
                    "text": z.text,
                    "low": z.low,
                    "high": z.high,
                    "tone": z.tone,
                    "low_excl": z.low_excl,
                    "high_incl": z.high_incl,
                }
                for z in g.zones
            ],
            "marks": _marks(g),
            "bands": _bands(g),
            "source": g.source,
            "url": g.url,
            "notes": list(g.notes),
        }
    return out
