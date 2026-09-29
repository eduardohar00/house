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
Busca en guías oficiales para respaldar cada recomendación. Esta revisión es independiente: parte solo de mi
expediente actual.

Reglas de fondo:
- Separa siempre lo que dijo o diagnosticó mi médico (consultas e informes) de lo que opinas tú. Escribe
  «mi médico indicó…» para lo primero y «mi hipótesis…» o «mi criterio…» para lo segundo.
- No sugieras descartar, estudiar o hacer algo que ya consta como hecho o descartado en mis consultas o informes.
  Si crees que aun así conviene repetirlo, di por qué.
- Los síntomas y observaciones que anoté y los medicamentos con reacción (bad_reaction) valen tanto como un
  resultado: relaciónalos con el resto y, si sugieren riesgo, ponlos entre los primeros puntos.
- Cuando un problema tenga varias causas posibles y ninguna esté confirmada (por ejemplo un sangrado), no digas
  que «no hay causa» ni elijas una sola: enumera las hipótesis, quién sostiene cada una (mi médico, mi
  observación o tú) y qué evidencia faltaría para decidir.
- Algunas fechas que anoté son aproximadas (lo dicen sus notas): trátalas como tales. Para cuándo empezó un
  padecimiento usa el año que trae el padecimiento en el expediente.

Estructura tu respuesta EXACTAMENTE con estos apartados (títulos con ##):

## Resumen ejecutivo
Máximo 5 puntos numerados, ordenados por prioridad (lo urgente primero). Formato EXACTO de cada punto:
`1. **Prioridad: título corto.** Una o dos frases: qué pasa y qué hacer.`
donde «Prioridad» es una sola palabra: Urgente, Pronto, Rutina o Informativo. Sin sub-listas ni párrafos
largos. Después de los puntos, no agregues nada más en este apartado.
## Lo que dicen tus datos
Un subtítulo en negritas por tema, en su propia línea, con su importancia entre paréntesis, por ejemplo
`**1. Aparato digestivo (lo más importante)**`. Debajo, viñetas con los hallazgos y, en una viñeta que empiece
con «**Mi opinión:**», qué podrían significar y qué tan importantes son.
## Relaciones que noté
Lista numerada; cada punto empieza con un título corto en negritas y sigue con la explicación: conexiones entre
estudios, padecimientos, medicamentos, suplementos, hábitos y antecedentes familiares que yo quizá no he notado,
y qué conviene revisar por ello.
## Estudios y chequeos que podrías considerar
Una tabla con estas columnas exactas: Estudio | Por qué en tu caso | Frecuencia / guía (nómbrala con el año) |
Prioridad (Urgente, Pronto, Rutina o Según síntomas). Después de la tabla, las vacunas en viñetas.
## Cuidados y hábitos
Viñetas; cada una empieza con el tema en negritas seguido de dos puntos (por ejemplo «**Reflujo:**»).
## Qué comentar con mi médico
Lista numerada y priorizada de preguntas o temas; cada una empieza con la pregunta en negritas.
## Datos que me faltan
Viñetas; cada una empieza con el dato que falta en negritas y dice qué cambiaría en tu análisis.
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
