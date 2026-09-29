"""Composición corporal (reportes InBody y similares): foto o PDF -> medidas propuestas que la persona confirma.

Claude transcribe lo impreso; el código revisa que los números sean coherentes entre sí (por ejemplo que
masa grasa / peso coincida con el porcentaje de grasa) y avisa lo que no cuadre. Nada entra al perfil sin
confirmación, y se conserva el original.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from ..providers import LLMRequest, Router
from . import health

SCHEMA = """
CREATE TABLE IF NOT EXISTS body_scan (
  document_id INTEGER PRIMARY KEY REFERENCES document(id) ON DELETE CASCADE,
  person_id   INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  measured_on TEXT,
  confirmed   INTEGER NOT NULL DEFAULT 0,
  data        TEXT NOT NULL
);
"""

# clave del reporte -> (nombre legible, unidad, tipo de medida en el perfil o None si solo se conserva en el reporte)
METRICS: dict[str, tuple[str, str, str | None]] = {
    "weight_kg": ("Peso", "kg", "weight_kg"),
    "skeletal_muscle_kg": ("Masa muscular esquelética", "kg", "skeletal_muscle_kg"),
    "body_fat_kg": ("Masa grasa", "kg", "body_fat_kg"),
    "body_fat_pct": ("Porcentaje de grasa corporal", "%", "body_fat_pct"),
    "lean_mass_kg": ("Masa magra", "kg", "lean_mass_kg"),
    "fat_free_mass_kg": ("Masa libre de grasa", "kg", "fat_free_mass_kg"),
    "total_body_water_l": ("Agua corporal total", "L", "body_water_l"),
    "protein_kg": ("Proteínas", "kg", "protein_kg"),
    "mineral_kg": ("Minerales", "kg", "mineral_kg"),
    "bone_mineral_kg": ("Mineral óseo", "kg", "bone_mineral_kg"),
    "waist_hip_ratio": ("Relación cintura-cadera", "", "whr"),
    "fitness_score": ("Puntuación de fitness", "puntos", "fitness_score"),
    "bmr_kcal": ("Metabolismo basal", "kcal", "bmr_kcal"),
    "bmi": ("IMC (del reporte)", "kg/m²", None),
}
REGIONS = ("brazo_izquierdo", "brazo_derecho", "tronco", "pierna_izquierda", "pierna_derecha")
EVALS = ("bajo", "normal", "alto", "fuerte", "excesivo", "muy alto")

SYSTEM = """Transcribes reportes de composición corporal (InBody u otros de bioimpedancia). Recibes una foto o una
página escaneada.

REGLAS
1. Copia solo números impresos; no calcules ni estimes nada. Si un dato no aparece o no se lee con seguridad,
   usa null.
2. «metrics»: una entrada por cada magnitud principal que veas (peso, masa muscular esquelética, masa grasa,
   porcentaje de grasa, masa magra, masa libre de grasa, agua corporal total, proteínas, minerales, mineral óseo,
   relación cintura-cadera, puntuación de fitness, metabolismo basal, IMC), con su valor, su intervalo normal
   impreso (normal_low, normal_high) y la evaluación impresa (bajo, normal, alto, fuerte, excesivo, muy alto)
   si aparece marcada.
3. «segments»: masa magra y grasa por región (brazos, tronco, piernas) con su evaluación, si el reporte la trae.
4. «measured_on»: fecha del reporte en AAAA-MM-DD. En México se escribe día.mes.año («4.9.2026» = 4 de septiembre
   de 2026). Pon la fecha tal como está impresa en «date_printed».
5. NO transcribas datos personales (nombre, ID, teléfono). Sí la altura, la edad y el sexo impresos.
6. En «notes» pon avisos impresos relevantes (por ejemplo objetivos de control de peso, grasa y músculo). Si no
   hay, null.
Responde solo con el JSON pedido."""


def scan_schema() -> dict:
    num = {"type": ["number", "null"]}
    text = {"type": ["string", "null"]}
    metric = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": list(METRICS)},
            "value": num,
            "normal_low": num,
            "normal_high": num,
            "evaluation": {"type": ["string", "null"], "enum": [*EVALS, None]},
        },
        "required": ["key", "value", "normal_low", "normal_high", "evaluation"],
        "additionalProperties": False,
    }
    segment = {
        "type": "object",
        "properties": {
            "region": {"type": "string", "enum": list(REGIONS)},
            "lean_kg": num,
            "lean_evaluation": {"type": ["string", "null"], "enum": [*EVALS, None]},
            "fat_kg": num,
            "fat_pct": num,
            "fat_evaluation": {"type": ["string", "null"], "enum": [*EVALS, None]},
        },
        "required": ["region", "lean_kg", "lean_evaluation", "fat_kg", "fat_pct", "fat_evaluation"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "device": text,
            "measured_on": text,
            "date_printed": text,
            "height_cm": num,
            "age": num,
            "sex": {"type": ["string", "null"], "enum": ["M", "F", None]},
            "metrics": {"type": "array", "items": metric},
            "segments": {"type": "array", "items": segment},
            "notes": text,
        },
        "required": [
            "device",
            "measured_on",
            "date_printed",
            "height_cm",
            "age",
            "sex",
            "metrics",
            "segments",
            "notes",
        ],
        "additionalProperties": False,
    }


def _n(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 3)
    except (TypeError, ValueError):
        return None


def clean(data: dict) -> dict:
    """Lo que devuelve el modelo, ajustado, y las advertencias de coherencia calculadas por el código."""
    when = (data.get("measured_on") or "").strip()[:10] or None
    try:
        when = date.fromisoformat(when).isoformat() if when else None
    except ValueError:
        when = None
    metrics: dict[str, dict] = {}
    for m in data.get("metrics") or []:
        key = m.get("key")
        if key not in METRICS or key in metrics or _n(m.get("value")) is None:
            continue
        name, unit, _ = METRICS[key]
        metrics[key] = {
            "key": key, "name": name, "unit": unit, "value": _n(m["value"]),
            "normal_low": _n(m.get("normal_low")), "normal_high": _n(m.get("normal_high")),
            "evaluation": m.get("evaluation") if m.get("evaluation") in EVALS else None,
        }  # fmt: skip
    segments = [
        {
            "region": s["region"],
            "lean_kg": _n(s.get("lean_kg")),
            "lean_evaluation": s.get("lean_evaluation"),
            "fat_kg": _n(s.get("fat_kg")),
            "fat_pct": _n(s.get("fat_pct")),
            "fat_evaluation": s.get("fat_evaluation"),
        }  # fmt: skip
        for s in (data.get("segments") or [])
        if s.get("region") in REGIONS
    ]
    out = {
        "device": (data.get("device") or "")[:60] or None,
        "measured_on": when,
        "date_printed": (data.get("date_printed") or "")[:30] or None,
        "height_cm": _n(data.get("height_cm")),
        "age": _n(data.get("age")),
        "sex": data.get("sex") if data.get("sex") in ("M", "F") else None,
        "metrics": list(metrics.values()),
        "segments": segments,
        "notes": (data.get("notes") or "")[:600] or None,
    }
    out["warnings"] = check(out)
    return out


def check(scan: dict) -> list[str]:
    """Revisiones deterministas: valores dentro de lo posible y números que deben cuadrar entre sí."""
    warnings: list[str] = []
    m = {x["key"]: x["value"] for x in scan["metrics"]}
    for key, value in m.items():
        kind = METRICS[key][2]
        if kind and kind in health.KINDS:
            (lo, hi), _, _ = health.KINDS[kind]
            if not lo <= value <= hi:
                warnings.append(
                    f"«{METRICS[key][0]}» = {value:g} parece fuera de lo posible: revísalo contra el original."
                )
    w, fat, pct = m.get("weight_kg"), m.get("body_fat_kg"), m.get("body_fat_pct")
    if w and fat and pct and abs(100 * fat / w - pct) > 1.5:
        warnings.append(
            f"La masa grasa ({fat:g} kg) y el porcentaje ({pct:g} %) no cuadran con el peso ({w:g} kg)."
        )
    ffm = m.get("fat_free_mass_kg")
    if w and fat and ffm and abs(w - fat - ffm) > 1.5:
        warnings.append("Peso, masa grasa y masa libre de grasa no suman bien: revisa esas cifras.")
    h, bmi = scan.get("height_cm"), m.get("bmi")
    if w and h and bmi and abs(w / (h / 100) ** 2 - bmi) > 0.8:
        warnings.append(f"El IMC impreso ({bmi:g}) no coincide con el peso y la altura del reporte.")
    if not scan.get("measured_on"):
        warnings.append("No pude leer la fecha del reporte: escríbela antes de guardar.")
    return warnings


def read_scan(router: Router, images: list[bytes]) -> dict:
    req = LLMRequest(
        task="extract",
        system=SYSTEM,
        user=f"Transcribe este reporte de composición corporal. Son {len(images)} imagen(es).",
        schema=scan_schema(),
        max_tokens=6000,
        images=tuple(images),
    )
    return clean(router.complete_json(req).data)


def save_confirmed(
    db: sqlite3.Connection, person_id: int, doc_id: int, when: str, keys: list[str], height_cm: float | None
) -> int:
    """Guarda como medidas del perfil las magnitudes elegidas y, si se pidió, actualiza la talla."""
    row = db.execute(
        "SELECT data FROM body_scan WHERE document_id = ? AND person_id = ?", (doc_id, person_id)
    ).fetchone()
    if row is None:
        raise health.HealthError(404, "No encontré ese reporte.")
    scan = json.loads(row["data"])
    values = {m["key"]: m["value"] for m in scan["metrics"]}
    saved = 0
    for key in keys:
        kind = METRICS.get(key, ("", "", None))[2]
        if kind is None or key not in values:
            continue
        health.add_measurement(
            db,
            person_id,
            {"kind": kind, "value": values[key], "measured_on": when, "notes": scan.get("device")},
            doc_id,
        )
        saved += 1
    if height_cm:
        prof = health.overview(db, person_id)["profile"]
        health.save_profile(db, person_id, {**prof, "height_cm": height_cm})
    db.execute("UPDATE body_scan SET confirmed = 1, measured_on = ? WHERE document_id = ?", (when, doc_id))
    return saved


def scans(db: sqlite3.Connection, person_id: int, limit: int = 12) -> list[dict]:
    rows = db.execute(
        "SELECT document_id, measured_on, data FROM body_scan WHERE person_id = ? AND confirmed = 1 "
        "ORDER BY measured_on DESC, document_id DESC LIMIT ?",
        (person_id, limit),
    )
    return [
        {"document_id": r["document_id"], "measured_on": r["measured_on"], **json.loads(r["data"])}
        for r in rows
    ]
