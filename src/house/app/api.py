"""API local de House. Solo escucha en 127.0.0.1 (ver __main__.py).

Permisos: cada persona ve solo su perfil; el admin ve todos y cada acceso a un perfil ajeno queda
en access_log. Solo el admin crea perfiles, asigna o restablece PIN y borra perfiles.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..config import Config, ProviderConfig
from ..normalize import summary as summary_mod
from ..normalize import terminology
from ..providers import ProviderError, Router
from ..providers.registry import BudgetExceeded, UsageLedger
from . import auth, ingest, store
from .vault import KeyProvider, Vault, keychain_key

COOKIE = "house_session"


class NewPerson(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    birth_date: str
    sex_at_birth: Literal["F", "M"]
    has_login: bool = True
    pin: str | None = None
    other_names: list[str] = []  # como aparece en los estudios, para quitarlo antes de enviar a la IA

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


class Decision(BaseModel):
    row_id: int
    accept: bool
    analyte_key: str | None = None
    value_num: float | None = None
    value_text: str | None = None


class ImagingDecision(BaseModel):
    position: int
    accept: bool
    performed_on: str | None = None
    study_name: str | None = None
    modality: str | None = None
    flag: str | None = None


class ImagingReview(BaseModel):
    decisions: list[ImagingDecision]


class Review(BaseModel):
    collected_on: str
    decisions: list[Decision]


# Por decisión de Eduardo, todo lo hace Claude. Un house.toml en la carpeta de datos lo cambia.
DEFAULT_CONFIG = Config(
    monthly_budget_usd=15.0,
    tasks={"extract": "claude", "interpret": "claude", "verify": "claude"},
    providers={
        "claude": ProviderConfig(
            name="claude",
            kind="anthropic",
            model="claude-opus-5-5",
            effort="medium",
            input_per_mtok=4.0,
            output_per_mtok=20.0,
        )
    },
)


# Sin clave de Anthropic: lector básico local (reglas), que no envía nada fuera de la Mac.
BASIC_CONFIG = Config(
    monthly_budget_usd=1e9,  # sin costo: no hay tope que aplicar
    tasks={"extract": "basico"},
    providers={"basico": ProviderConfig(name="basico", kind="mock")},
)
WEB = Path(__file__).parent / "web"


def reader_router(data_dir: Path, vault: Vault) -> tuple[Router, str]:
    """Router para leer estudios y cómo se llama el lector ('claude' o 'basico')."""
    ledger = UsageLedger(data_dir / "ai_usage.jsonl")
    toml = data_dir / "house.toml"
    if toml.exists():
        return Router(Config.load(toml), ledger=ledger), "configurado"
    key = vault.get_secret("anthropic")
    if not key:
        return Router(BASIC_CONFIG), "basico"
    import anthropic

    from ..providers.anthropic_provider import AnthropicProvider

    c = DEFAULT_CONFIG.providers["claude"]
    provider = AnthropicProvider(
        c.name,
        c.model,
        effort=c.effort,
        input_per_mtok=c.input_per_mtok,
        output_per_mtok=c.output_per_mtok,
        client=anthropic.Anthropic(api_key=key),
    )
    return Router(DEFAULT_CONFIG, ledger=ledger, overrides={"claude": provider}), "claude"


def _public(p: sqlite3.Row) -> dict:
    return {
        "id": p["id"],
        "display_name": p["display_name"],
        "birth_date": p["birth_date"],
        "sex_at_birth": p["sex_at_birth"],
        "is_admin": bool(p["is_admin"]),
        "has_login": bool(p["has_login"]),
    }


def create_app(
    data_dir: Path | None = None,
    key_provider: KeyProvider = keychain_key,
    router: Router | None = None,
) -> FastAPI:
    data_dir = data_dir or store.default_data_dir()
    db = store.connect(data_dir)
    db.executescript(ingest.SCHEMA)
    ingest.migrate_imaging(db)
    ingest.repair_references(db)
    fixed_router = router
    vault = Vault(data_dir / "originals", key_provider)
    app = FastAPI(title="House", docs_url=None, redoc_url=None, openapi_url=None)
    # Rechaza peticiones con otro Host (defensa contra DNS rebinding desde páginas externas).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.db, app.state.vault = db, vault

    def current_router() -> tuple[Router, str]:
        return (fixed_router, "prueba") if fixed_router else reader_router(data_dir, vault)

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

    def save_aliases(person_id: int, names: list[str]) -> None:
        for n in names:
            if n.strip():
                db.execute("INSERT INTO person_alias(person_id, name) VALUES(?,?)", (person_id, n.strip()))

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
        save_aliases(cur.lastrowid, body.other_names)
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
        save_aliases(cur.lastrowid, body.other_names)
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

    def load_observations(person_id: int) -> list[dict]:
        rows = db.execute(
            "SELECT o.analyte_key, o.printed_name, o.value_num, o.value_text, o.unit, o.ref_low, o.ref_high, "
            "o.ref_printed, o.status, o.method, o.collected_on, o.document_id, d.title AS document_title "
            "FROM observation o JOIN document d ON d.id = o.document_id WHERE o.person_id = ? "
            "ORDER BY o.collected_on, o.analyte_key",
            (person_id,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/people/{person_id}/observations")
    def observations(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_resultados")
        return load_observations(person_id)

    @app.get("/api/people/{person_id}/overview")
    def overview(person_id: int, actor: Me) -> dict:
        """Resultados y resumen (vigente vs historial) en una sola consulta: un solo registro de acceso."""
        subject(person_id, actor, "ver_resultados")
        obs = load_observations(person_id)
        return {"observations": obs, "summary": summary_mod.summarize(obs)}

    def document(doc_id: int, actor: sqlite3.Row, action: str) -> sqlite3.Row:
        doc = db.execute("SELECT * FROM document WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            raise HTTPException(404, "Estudio no encontrado")
        subject(doc["person_id"], actor, action)
        return doc

    @app.post("/api/people/{person_id}/documents")
    async def upload(person_id: int, actor: Me, file: Annotated[UploadFile, File()]) -> dict:
        person = subject(person_id, actor, "subir_estudio")
        data = await file.read(ingest.MAX_BYTES + 1)
        router, reader = current_router()
        try:
            done = ingest.ingest_pdf(db, vault, router, person, file.filename or "Estudio.pdf", data)
        except ingest.IngestError as e:
            raise HTTPException(e.status, {"message": e.message, "document_id": e.document_id}) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            # El mensaje del proveedor no incluye contenido del documento (ver providers/base.py).
            raise HTTPException(502, f"No se pudo leer con la IA: {e}") from None
        return {"document_id": done.document_id, "rows": done.rows, "kind": done.kind, "reader": reader}

    @app.get("/api/people/{person_id}/documents")
    def list_documents(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_estudios")
        rows = db.execute(
            "SELECT d.id, d.doc_type, d.title, d.collected_on, d.review_state, d.uploaded_at, "
            "(SELECT COUNT(*) FROM observation o WHERE o.document_id = d.id) "
            "+ (SELECT COUNT(*) FROM imaging_study i WHERE i.document_id = d.id) AS results "
            "FROM document d WHERE person_id = ? ORDER BY COALESCE(collected_on, uploaded_at) DESC",
            (person_id,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: int, actor: Me) -> dict:
        document(doc_id, actor, "ver_estudio")
        return ingest.review_payload(db, doc_id)

    @app.get("/api/documents/{doc_id}/file")
    def get_file(doc_id: int, actor: Me) -> Response:
        doc = document(doc_id, actor, "ver_original")
        return Response(
            vault.get(doc["file_path"]),
            media_type="application/pdf",
            headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
        )

    @app.post("/api/documents/{doc_id}/reread")
    def reread(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "releer_estudio")
        person = db.execute("SELECT * FROM person WHERE id = ?", (doc["person_id"],)).fetchone()
        router, reader = current_router()
        try:
            done = ingest.reread(db, vault, router, doc_id, person)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo leer con la IA: {e}") from None
        return {"document_id": doc_id, "rows": done.rows, "kind": done.kind, "reader": reader}

    @app.get("/api/documents/{doc_id}/layout")
    def get_layout(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "ver_original")
        rows = db.execute("SELECT * FROM extraction_row WHERE document_id = ?", (doc_id,)).fetchall()
        return ingest.page_layout(vault.get(doc["file_path"]), rows)

    @app.get("/api/documents/{doc_id}/pages/{n}")
    def get_page(doc_id: int, n: int, actor: Me) -> Response:
        doc = document(doc_id, actor, "ver_original")
        try:
            png = ingest.render_page(vault.get(doc["file_path"]), n)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/documents/{doc_id}/review")
    def review(doc_id: int, body: Review, actor: Me) -> dict:
        document(doc_id, actor, "revisar_estudio")
        try:
            saved = ingest.confirm_review(
                db,
                doc_id,
                actor["id"],
                body.collected_on,
                [d.model_dump(exclude_unset=True) for d in body.decisions],
            )
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        except ValueError:
            raise HTTPException(422, "Fecha inválida (usa AAAA-MM-DD).") from None
        return {"saved": saved}

    @app.post("/api/documents/{doc_id}/review-imaging")
    def review_imaging(doc_id: int, body: ImagingReview, actor: Me) -> dict:
        document(doc_id, actor, "revisar_estudio")
        try:
            saved = ingest.confirm_imaging_review(
                db, doc_id, actor["id"], [d.model_dump(exclude_unset=True) for d in body.decisions]
            )
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return {"saved": saved}

    @app.get("/api/people/{person_id}/imaging")
    def imaging_list(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_imagen")
        rows = db.execute(
            "SELECT i.id, i.document_id, i.modality, i.study_name, i.performed_on, i.technique, "
            "i.indication, i.findings, i.prior, i.conclusion, i.suggestions, i.radiologist, i.site, i.flag, "
            "d.title AS document_title FROM imaging_study i LEFT JOIN document d ON d.id = i.document_id "
            "WHERE i.person_id = ? ORDER BY i.performed_on DESC, i.id",
            (person_id,),
        )
        return [dict(r) for r in rows]

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "borrar_estudio")
        db.execute("DELETE FROM imaging_study WHERE document_id = ?", (doc_id,))  # sin restos huérfanos
        db.execute("DELETE FROM document WHERE id = ?", (doc_id,))
        vault.delete(doc["file_path"])
        return {"ok": True}

    @app.get("/api/catalog")
    def catalog() -> dict:
        return {
            a.key: {"name": a.name, "unit": a.unit, "group": a.group, "kind": a.kind}
            for a in terminology.CATALOG
        }

    @app.get("/api/settings")
    def settings(_: Admin) -> dict:
        _, reader = current_router()
        month = db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS usd, COUNT(*) AS n FROM ai_call "
            "WHERE strftime('%Y-%m', at) = strftime('%Y-%m', 'now')"
        ).fetchone()
        return {
            "reader": reader,
            "budget_usd": DEFAULT_CONFIG.monthly_budget_usd,
            "month_usd": round(month["usd"], 4),
            "month_calls": month["n"],
            "data_dir": str(data_dir),
        }

    class ApiKey(BaseModel):
        key: str

    @app.put("/api/settings/anthropic-key")
    def set_key(body: ApiKey, _: Admin) -> dict:
        key = body.key.strip()
        if not key.startswith("sk-ant-") or len(key) < 30:
            raise HTTPException(422, "Esa no parece una clave de Anthropic (empieza con «sk-ant-»).")
        vault.put_secret("anthropic", key)
        return {"reader": "claude"}

    @app.delete("/api/settings/anthropic-key")
    def delete_key(_: Admin) -> dict:
        vault.delete_secret("anthropic")
        return {"reader": "basico"}

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

    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
