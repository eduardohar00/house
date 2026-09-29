"""PIN y sesiones. El PIN nunca se guarda: solo un hash scrypt con sal."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time

PIN_RE = re.compile(r"^\d{4,8}$")
MAX_FAILURES = 5
LOCK_SECONDS = 5 * 60
IDLE_SECONDS = 15 * 60  # bloqueo por inactividad

_N, _R, _P = 2**14, 8, 1


class AuthError(Exception):
    pass


def hash_pin(pin: str) -> str:
    if not PIN_RE.match(pin):
        raise AuthError("El PIN debe tener de 4 a 8 dígitos.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def check_pin(pin: str, stored: str | None) -> bool:
    if not stored or not PIN_RE.match(pin):
        return False
    _, n, r, p, salt, digest = stored.split("$")
    got = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(got.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(db: sqlite3.Connection, person_id: int, pin: str, now: float | None = None) -> str:
    """Devuelve un token de sesión. Tras 5 PIN incorrectos, bloquea 5 minutos a esa persona."""
    now = time.time() if now is None else now
    person = db.execute("SELECT pin_hash, has_login FROM person WHERE id = ?", (person_id,)).fetchone()
    if person is None or not person["has_login"]:
        raise AuthError("PIN incorrecto.")
    fail = db.execute(
        "SELECT count, locked_until FROM login_failure WHERE person_id = ?", (person_id,)
    ).fetchone()
    if fail and fail["locked_until"] and fail["locked_until"] > now:
        minutes = int((fail["locked_until"] - now) // 60) + 1
        raise AuthError(f"Demasiados intentos. Espera {minutes} min.")
    if not check_pin(pin, person["pin_hash"]):
        count = (fail["count"] if fail else 0) + 1
        locked = now + LOCK_SECONDS if count >= MAX_FAILURES else None
        db.execute(
            "INSERT INTO login_failure(person_id, count, locked_until) VALUES(?,?,?) "
            "ON CONFLICT(person_id) DO UPDATE SET "
            "count = excluded.count, locked_until = excluded.locked_until",
            (person_id, 0 if locked else count, locked),
        )
        raise AuthError("PIN incorrecto.")
    db.execute("DELETE FROM login_failure WHERE person_id = ?", (person_id,))
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO session(token_hash, person_id, created_at, last_seen) VALUES(?,?,?,?)",
        (_token_hash(token), person_id, now, now),
    )
    return token


def current_person(db: sqlite3.Connection, token: str | None, now: float | None = None) -> sqlite3.Row | None:
    """Persona de la sesión, o None si no hay sesión o expiró por inactividad."""
    if not token:
        return None
    now = time.time() if now is None else now
    th = _token_hash(token)
    s = db.execute("SELECT person_id, last_seen FROM session WHERE token_hash = ?", (th,)).fetchone()
    if s is None:
        return None
    if now - s["last_seen"] > IDLE_SECONDS:
        db.execute("DELETE FROM session WHERE token_hash = ?", (th,))
        return None
    db.execute("UPDATE session SET last_seen = ? WHERE token_hash = ?", (now, th))
    return db.execute("SELECT * FROM person WHERE id = ?", (s["person_id"],)).fetchone()


def logout(db: sqlite3.Connection, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM session WHERE token_hash = ?", (_token_hash(token),))
