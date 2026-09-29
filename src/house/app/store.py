"""Base de datos local de la app: dónde vive y cómo se abre.

Los datos viven FUERA del repositorio, en ~/Library/Application Support/House (o HOUSE_DATA_DIR).
El disco va cifrado por FileVault; los originales, además, con la llave de vault.py.
"""

from __future__ import annotations

import os
import sqlite3
from importlib.resources import files
from pathlib import Path

# Tablas propias de la app (sesiones y bloqueo por intentos), aparte del contrato de datos clínicos.
_APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
  token_hash TEXT PRIMARY KEY,
  person_id  INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  last_seen  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failure (
  person_id    INTEGER PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
  count        INTEGER NOT NULL DEFAULT 0,
  locked_until REAL
);
"""


def default_data_dir() -> Path:
    env = os.environ.get("HOUSE_DATA_DIR")
    return Path(env) if env else Path.home() / "Library" / "Application Support" / "House"


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db_path = data_dir / "house.db"
    new = not db_path.exists()
    db = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    if new:
        db.executescript(files("house.db").joinpath("schema.sql").read_text(encoding="utf-8"))
    db.executescript(_APP_SCHEMA)
    os.chmod(db_path, 0o600)
    return db
