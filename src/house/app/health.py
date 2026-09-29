"""Perfil de salud (talla, hábitos…) y medidas con fecha (peso, presión, cintura…).

Son los datos que un análisis de laboratorio no trae y que cambian qué estudios o cuidados tienen sentido
(tabaquismo, alcohol, actividad física, peso). El asistente los usa para orientar; no son un diagnóstico.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS health_profile (
  person_id INTEGER PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
  height_cm REAL, blood_type TEXT, smoking TEXT, smoking_detail TEXT, alcohol TEXT, alcohol_detail TEXT,
  exercise TEXT, exercise_detail TEXT, sleep_hours REAL, diet TEXT, occupation TEXT, notes TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS measurement (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  kind TEXT NOT NULL
    CHECK (kind IN ('weight_kg', 'blood_pressure', 'heart_rate', 'waist_cm', 'body_fat_pct')),
  value REAL NOT NULL, value2 REAL,                    -- presión: value = sistólica, value2 = diastólica
  measured_on TEXT NOT NULL, notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_measurement ON measurement (person_id, kind, measured_on);
"""

BLOOD_TYPES = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
CHOICES = {
    "blood_type": BLOOD_TYPES,
    "smoking": ("Nunca", "Exfumador", "Actual"),
    "alcohol": ("Nunca", "Ocasional", "Semanal", "Diario"),
    "exercise": ("Sedentario", "1 a 2 días por semana", "3 a 4 días por semana", "5 o más días por semana"),
}
TEXTS = ("smoking_detail", "alcohol_detail", "exercise_detail", "diet", "occupation", "notes")
NUMBERS = {"height_cm": (50, 250), "sleep_hours": (2, 16)}
KINDS = {  # tipo: (rango del valor, rango del segundo valor, unidad)
    "weight_kg": ((10, 400), None, "kg"),
    "blood_pressure": ((50, 300), (20, 200), "mmHg"),
    "heart_rate": ((25, 250), None, "lpm"),
    "waist_cm": ((30, 250), None, "cm"),
    "body_fat_pct": ((2, 70), None, "%"),
}


class HealthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _num(v: Any, label: str, lo: float, hi: float) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    try:
        n = float(str(v).replace(",", "."))
    except ValueError:
        raise HealthError(422, f"{label}: escribe un número.") from None
    if not lo <= n <= hi:
        raise HealthError(422, f"{label}: debe estar entre {lo:g} y {hi:g}.")
    return n


def save_profile(db: sqlite3.Connection, person_id: int, data: dict) -> None:
    values: dict[str, Any] = {}
    for col, options in CHOICES.items():
        v = (data.get(col) or "").strip() or None
        if v is not None and v not in options:
            raise HealthError(422, f"Valor no válido para «{col}».")
        values[col] = v
    for col, (lo, hi) in NUMBERS.items():
        values[col] = _num(
            data.get(col), {"height_cm": "La talla", "sleep_hours": "Las horas de sueño"}[col], lo, hi
        )
    for col in TEXTS:
        t = " ".join(str(data.get(col) or "").split())
        if len(t) > 500:
            raise HealthError(422, "Un texto es demasiado largo (máximo 500 caracteres).")
        values[col] = t or None
    cols = ", ".join(values)
    marks = ", ".join("?" * (len(values) + 2))
    sets = ", ".join(f"{c} = excluded.{c}" for c in [*values, "updated_at"])
    db.execute(
        f"INSERT INTO health_profile(person_id, {cols}, updated_at) VALUES({marks}) "  # noqa: S608
        f"ON CONFLICT(person_id) DO UPDATE SET {sets}",
        (person_id, *values.values(), datetime.now().isoformat(timespec="seconds")),
    )


def add_measurement(db: sqlite3.Connection, person_id: int, data: dict) -> int:
    kind = data.get("kind")
    if kind not in KINDS:
        raise HealthError(422, "Tipo de medida desconocido.")
    (lo, hi), second, _unit = KINDS[kind]
    value = _num(data.get("value"), "El valor", lo, hi)
    if value is None:
        raise HealthError(422, "Escribe el valor.")
    value2 = None
    if second:
        value2 = _num(data.get("value2"), "La presión diastólica", *second)
        if value2 is None:
            raise HealthError(422, "Escribe la presión sistólica y la diastólica.")
        if value2 >= value:
            raise HealthError(422, "La sistólica (la alta) debe ser mayor que la diastólica.")
    when = (data.get("measured_on") or date.today().isoformat()).strip()
    try:
        d = date.fromisoformat(when)
    except ValueError:
        raise HealthError(422, "La fecha no es válida (usa AAAA-MM-DD).") from None
    if d > date.today():
        raise HealthError(422, "La fecha no puede ser futura.")
    notes = " ".join(str(data.get("notes") or "").split())[:300] or None
    cur = db.execute(
        "INSERT INTO measurement(person_id, kind, value, value2, measured_on, notes) VALUES(?,?,?,?,?,?)",
        (person_id, kind, value, value2, when, notes),
    )
    return cur.lastrowid


def remove_measurement(db: sqlite3.Connection, person_id: int, measurement_id: int) -> None:
    cur = db.execute("DELETE FROM measurement WHERE id = ? AND person_id = ?", (measurement_id, person_id))
    if cur.rowcount == 0:
        raise HealthError(404, "No encontré esa medida en este perfil.")


def bmi(height_cm: float | None, weight_kg: float | None) -> float | None:
    if not height_cm or not weight_kg:
        return None
    return round(weight_kg / (height_cm / 100) ** 2, 1)


def overview(db: sqlite3.Connection, person_id: int) -> dict:
    row = db.execute("SELECT * FROM health_profile WHERE person_id = ?", (person_id,)).fetchone()
    profile = {k: row[k] for k in row.keys() if k not in ("person_id",)} if row else {}
    measurements = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM measurement WHERE person_id = ? ORDER BY measured_on DESC, id DESC", (person_id,)
        )
    ]
    latest: dict[str, dict] = {}
    for m in measurements:
        latest.setdefault(m["kind"], m)
    weight = latest.get("weight_kg", {}).get("value")
    return {
        "profile": profile,
        "measurements": measurements,
        "latest": latest,
        "bmi": bmi(profile.get("height_cm"), weight),
        "choices": {k: list(v) for k, v in CHOICES.items()},
    }
