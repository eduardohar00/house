"""API local de House. Solo escucha en 127.0.0.1 (ver __main__.py).

Permisos: cada persona ve solo su perfil; el admin ve todos y cada acceso a un perfil ajeno queda
en access_log. Solo el admin crea perfiles, asigna o restablece PIN y borra perfiles.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, field_validator

from . import auth, store
from .vault import KeyProvider, Vault, keychain_key

COOKIE = "house_session"


class NewPerson(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    birth_date: str
    sex_at_birth: Literal["F", "M"]
    has_login: bool = True
    pin: str | None = None

    @field_validator("birth_date")
    @classmethod
    def _iso(cls, v: str) -> str:
        date.fromisoformat(v)
        return v


class Login(BaseModel):
    person_id: int
    pin: str


class PinReset(BaseModel):
    pin: str


def _public(p: sqlite3.Row) -> dict:
    return {
        "id": p["id"],
        "display_name": p["display_name"],
        "birth_date": p["birth_date"],
        "sex_at_birth": p["sex_at_birth"],
        "is_admin": bool(p["is_admin"]),
        "has_login": bool(p["has_login"]),
    }


def create_app(data_dir: Path | None = None, key_provider: KeyProvider = keychain_key) -> FastAPI:
    data_dir = data_dir or store.default_data_dir()
    db = store.connect(data_dir)
    vault = Vault(data_dir / "originals", key_provider)
    app = FastAPI(title="House", docs_url=None, redoc_url=None, openapi_url=None)
    # Rechaza peticiones con otro Host (defensa contra DNS rebinding desde páginas externas).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.db, app.state.vault = db, vault

    @app.middleware("http")
    async def _no_cross_site_writes(request: Request, call_next):
        # Las escrituras deben venir de la propia app (cabecera que un formulario externo no puede poner).
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-house") != "1":
            return Response("Petición no permitida", status_code=403)
        return await call_next(request)

    def me(request: Request) -> sqlite3.Row:
        person = auth.current_person(db, request.cookies.get(COOKIE))
        if person is None:
            raise HTTPException(401, "Sesión cerrada")
        return person

    Me = Annotated[sqlite3.Row, Depends(me)]

    def admin(person: Me) -> sqlite3.Row:
        if not person["is_admin"]:
            raise HTTPException(403, "Solo el administrador")
        return person

    Admin = Annotated[sqlite3.Row, Depends(admin)]

    def subject(person_id: int, actor: sqlite3.Row, action: str) -> sqlite3.Row:
        """Perfil al que se accede, verificando permisos; el admin en perfil ajeno queda registrado."""
        if person_id != actor["id"] and not actor["is_admin"]:
            raise HTTPException(403, "Solo puedes ver tu propio perfil")
        row = db.execute("SELECT * FROM person WHERE id = ?", (person_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Perfil no encontrado")
        if person_id != actor["id"]:
            db.execute(
                "INSERT INTO access_log(actor_id, subject_id, action) VALUES(?,?,?)",
                (actor["id"], person_id, action),
            )
        return row

    def set_cookie(resp: Response, token: str) -> None:
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=False, path="/")

    @app.get("/api/status")
    def status() -> dict:
        has_admin = db.execute("SELECT 1 FROM person WHERE is_admin = 1").fetchone() is not None
        return {"needs_setup": not has_admin}

    @app.post("/api/setup")
    def setup(body: NewPerson, resp: Response) -> dict:
        """Primer uso: crea al administrador. Solo funciona si todavía no hay uno."""
        if db.execute("SELECT 1 FROM person WHERE is_admin = 1").fetchone():
            raise HTTPException(409, "Ya hay un administrador")
        try:
            pin_hash = auth.hash_pin(body.pin or "")
        except auth.AuthError as e:
            raise HTTPException(422, str(e)) from None
        cur = db.execute(
            "INSERT INTO person(display_name, birth_date, sex_at_birth, is_admin, has_login, pin_hash) "
            "VALUES(?,?,?,1,1,?)",
            (body.display_name, body.birth_date, body.sex_at_birth, pin_hash),
        )
        set_cookie(resp, auth.login(db, cur.lastrowid, body.pin or ""))
        return {"id": cur.lastrowid}

    @app.get("/api/profiles")
    def profiles() -> list[dict]:
        """Para la pantalla de bloqueo: solo nombre de quienes tienen acceso propio."""
        rows = db.execute(
            "SELECT id, display_name FROM person WHERE has_login = 1 ORDER BY is_admin DESC, id"
        )
        return [dict(r) for r in rows]

    @app.post("/api/login")
    def do_login(body: Login, resp: Response) -> dict:
        try:
            set_cookie(resp, auth.login(db, body.person_id, body.pin))
        except auth.AuthError as e:
            raise HTTPException(401, str(e)) from None
        return {"ok": True}

    @app.post("/api/logout")
    def do_logout(request: Request, resp: Response) -> dict:
        auth.logout(db, request.cookies.get(COOKIE))
        resp.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def get_me(person: Me) -> dict:
        return _public(person)

    @app.get("/api/people")
    def people(person: Me) -> list[dict]:
        if not person["is_admin"]:
            return [_public(person)]
        return [_public(p) for p in db.execute("SELECT * FROM person ORDER BY is_admin DESC, id")]

    @app.post("/api/people")
    def add_person(body: NewPerson, _: Admin) -> dict:
        pin_hash = None
        if body.has_login:
            try:
                pin_hash = auth.hash_pin(body.pin or "")
            except auth.AuthError as e:
                raise HTTPException(422, str(e)) from None
        cur = db.execute(
            "INSERT INTO person(display_name, birth_date, sex_at_birth, is_admin, has_login, pin_hash) "
            "VALUES(?,?,?,0,?,?)",
            (body.display_name, body.birth_date, body.sex_at_birth, int(body.has_login), pin_hash),
        )
        return {"id": cur.lastrowid}

    @app.put("/api/people/{person_id}/pin")
    def reset_pin(person_id: int, body: PinReset, actor: Admin) -> dict:
        subject(person_id, actor, "restablecer_pin")
        try:
            pin_hash = auth.hash_pin(body.pin)
        except auth.AuthError as e:
            raise HTTPException(422, str(e)) from None
        db.execute("UPDATE person SET pin_hash = ?, has_login = 1 WHERE id = ?", (pin_hash, person_id))
        db.execute("DELETE FROM login_failure WHERE person_id = ?", (person_id,))
        db.execute("DELETE FROM session WHERE person_id = ?", (person_id,))
        return {"ok": True}

    @app.delete("/api/people/{person_id}")
    def delete_person(person_id: int, actor: Admin) -> dict:
        if person_id == actor["id"]:
            raise HTTPException(409, "El administrador no puede borrarse a sí mismo")
        subject(person_id, actor, "borrar_perfil")
        files = [
            r["file_path"]
            for r in db.execute("SELECT file_path FROM document WHERE person_id = ?", (person_id,))
        ]
        db.execute("DELETE FROM access_log WHERE subject_id = ? OR actor_id = ?", (person_id, person_id))
        db.execute("DELETE FROM person WHERE id = ?", (person_id,))
        for f in files:
            vault.delete(f)
        return {"ok": True}

    @app.get("/api/people/{person_id}/observations")
    def observations(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_resultados")
        rows = db.execute(
            "SELECT analyte_key, value_num, value_text, unit, ref_low, ref_high, ref_printed, status, "
            "method, collected_on, document_id FROM observation WHERE person_id = ? "
            "ORDER BY collected_on, analyte_key",
            (person_id,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/access-log")
    def access_log(actor: Me) -> list[dict]:
        """Cada persona puede ver quién entró a su perfil; el admin ve todo."""
        where, args = ("", ()) if actor["is_admin"] else ("WHERE subject_id = ?", (actor["id"],))
        rows = db.execute(
            "SELECT a.at, a.action, p.display_name AS actor, s.display_name AS subject FROM access_log a "
            "JOIN person p ON p.id = a.actor_id JOIN person s ON s.id = a.subject_id "
            f"{where} ORDER BY a.id DESC",
            args,
        )
        return [dict(r) for r in rows]

    return app
