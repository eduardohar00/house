"""Revisión integral: Claude analiza TODO el historial con guías oficiales y deja un informe guardado.

Tarda unos minutos, así que corre en segundo plano y la pantalla consulta su avance. Cada revisión queda
guardada con la fecha para poder compararla con la siguiente. Es orientación informativa, no un diagnóstico.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from ..providers import BudgetExceeded, ProviderError, Router
from . import assistant

SCHEMA = """
CREATE TABLE IF NOT EXISTS review (
  id          INTEGER PRIMARY KEY,
  person_id   INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  status      TEXT NOT NULL CHECK (status IN ('running', 'done', 'error')),
  created_at  TEXT NOT NULL,
  finished_at TEXT,
  model       TEXT,
  cost_usd    REAL,
  content     TEXT,
  sources     TEXT,                                   -- fuentes del expediente citadas (JSON)
  web_sources TEXT,                                   -- guías y páginas web consultadas (JSON)
  web_note    TEXT,
  error       TEXT
);
"""

PROMPT = """Haz una REVISIÓN INTEGRAL de mi historial de salud. Empieza con get_full_history y profundiza con las
demás herramientas donde haga falta (por ejemplo los informes completos de endoscopia, biopsias y tomografía).
Busca en guías oficiales para respaldar cada recomendación.

Estructura tu respuesta EXACTAMENTE con estos apartados (títulos con ##):
## Resumen ejecutivo
Lo más importante en 5 renglones como máximo, con lo urgente primero.
## Lo que dicen tus datos
Padecimientos y hallazgos relevantes, con tu opinión de qué podrían significar y qué tan importantes son.
## Relaciones que noté
Conexiones entre estudios, padecimientos, medicamentos, suplementos, hábitos y antecedentes familiares que yo
quizá no he notado, y qué conviene revisar por ello.
## Estudios y chequeos que podrías considerar
Para cada uno: qué es, por qué en mi caso (edad, sexo, antecedentes, hábitos, resultados), cada cuánto
según la guía (nómbrala con el año) y prioridad (urgente / pronto / rutina).
## Cuidados y hábitos
Recomendaciones concretas según mi perfil y mis hallazgos.
## Qué comentar con mi médico
Lista priorizada de preguntas o temas.
## Datos que me faltan
Qué información adicional cambiaría tu análisis.
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def start(db: sqlite3.Connection, router: Router, person: sqlite3.Row, names: list[str]) -> int:
    """Crea la revisión y la ejecuta en segundo plano; si ya hay una en curso, devuelve esa."""
    running = db.execute(
        "SELECT id FROM review WHERE person_id = ? AND status = 'running'", (person["id"],)
    ).fetchone()
    if running:
        return running["id"]
    cur = db.execute(
        "INSERT INTO review(person_id, status, created_at) VALUES(?, 'running', ?)", (person["id"], _now())
    )
    rid, person_id = cur.lastrowid, person["id"]
    threading.Thread(
        target=_run, args=(db, router, rid, person_id, names), daemon=True, name=f"house-review-{rid}"
    ).start()
    return rid


def _run(db: sqlite3.Connection, router: Router, rid: int, person_id: int, names: list[str]) -> None:
    try:
        person = db.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
        out = assistant.ask(router, db, person, names, [{"role": "user", "content": PROMPT}], deep=True)
        db.execute(
            "UPDATE review SET status = 'done', finished_at = ?, model = ?, cost_usd = ?, content = ?, "
            "sources = ?, web_sources = ?, web_note = ? WHERE id = ?",
            (
                _now(),
                "claude",
                out["usage"]["cost_usd"],
                out["answer"],
                json.dumps(out["sources"], ensure_ascii=False),
                json.dumps(out["web_sources"], ensure_ascii=False),
                out["web_note"],
                rid,
            ),
        )
    except BudgetExceeded:
        _fail(
            db,
            rid,
            "Se alcanzó el tope mensual de gasto en IA (súbelo en Configuración o espera al mes siguiente).",
        )
    except ProviderError as e:
        _fail(db, rid, f"No se pudo completar la revisión: {e}")
    except Exception:  # noqa: BLE001 - el error se guarda para mostrarlo; nunca tumba a House
        _fail(db, rid, "La revisión falló por un error inesperado. Intenta de nuevo.")


def _fail(db: sqlite3.Connection, rid: int, message: str) -> None:
    db.execute(
        "UPDATE review SET status = 'error', finished_at = ?, error = ? WHERE id = ?", (_now(), message, rid)
    )


def interrupted(db: sqlite3.Connection) -> None:
    """Al arrancar: una revisión que estaba en curso cuando House se apagó ya no va a terminar."""
    db.execute(
        "UPDATE review SET status = 'error', finished_at = ?, error = 'House se reinició antes de terminar.' "
        "WHERE status = 'running'",
        (_now(),),
    )


def latest_done(db: sqlite3.Connection, person_id: int) -> dict | None:
    """La revisión integral terminada más reciente (para que las preguntas se apoyen en ella)."""
    r = db.execute(
        "SELECT id, finished_at, content FROM review WHERE person_id = ? AND status = 'done' "
        "AND content IS NOT NULL ORDER BY id DESC LIMIT 1",
        (person_id,),
    ).fetchone()
    return dict(r) if r else None


def listing(db: sqlite3.Connection, person_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT id, status, created_at, finished_at, cost_usd, error FROM review WHERE person_id = ? "
        "ORDER BY id DESC",
        (person_id,),
    )
    return [dict(r) for r in rows]


def detail(db: sqlite3.Connection, review_id: int) -> dict | None:
    r = db.execute("SELECT * FROM review WHERE id = ?", (review_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    for k in ("sources", "web_sources"):
        d[k] = json.loads(d[k]) if d.get(k) else []
    return d
