"""API local de House. Solo escucha en 127.0.0.1 (ver __main__.py).

Permisos: cada persona ve solo su perfil; el admin ve todos y cada acceso a un perfil ajeno queda
en access_log. Solo el admin crea perfiles, asigna o restablece PIN y borra perfiles.
"""

import json
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from ..config import Config, ProviderConfig
from ..normalize import explanations, terminology
from ..normalize import summary as summary_mod
from ..providers import ProviderError, Router
from ..providers.registry import BudgetExceeded, UsageLedger
from . import assistant, auth, backup, clinical, drugs, ingest, store
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
    printed_value: str | None = None  # al indicar el análisis de una fila no reconocida: lo que dice el PDF
    ignore: bool = False  # no es un resultado (control del laboratorio, leyenda): no se guarda ni se reclama


class ManualResult(BaseModel):
    analyte_key: str
    value: str
    unit: str | None = None
    ref: str | None = None


class ImagingDecision(BaseModel):
    position: int
    accept: bool
    performed_on: str | None = None
    study_name: str | None = None
    modality: str | None = None
    flag: str | None = None


class ImagingReview(BaseModel):
    decisions: list[ImagingDecision]


class RxDecision(BaseModel):
    index: int
    accept: bool
    name: str = ""
    active_ingredient: str | None = None
    brand: str | None = None
    dose: str | None = None
    frequency: str | None = None
    duration: str | None = None
    instructions: str | None = None
    active: bool = True


class RxReview(BaseModel):
    prescription_date: str | None = None
    prescriber: str | None = None
    diagnosis: str | None = None
    problem_id: int | None = None
    decisions: list[RxDecision]


class ChatTurn(BaseModel):
    role: str
    content: str


class AssistantQuestion(BaseModel):
    messages: list[ChatTurn]


class Review(BaseModel):
    collected_on: str
    decisions: list[Decision]
    manual: list[ManualResult] = []
    complete: bool = False  # completar un estudio ya revisado (solo lo que falta)


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


def reader_router(data_dir: Path, vault: Vault) -> tuple[Router, Router | None, str]:
    """(lector principal, lector de respaldo, nombre). Primero el básico, local y gratis; Claude solo si el
    básico no entiende el formato de un estudio. Sin clave de Anthropic no hay respaldo."""
    ledger = UsageLedger(data_dir / "ai_usage.jsonl")
    toml = data_dir / "house.toml"
    if toml.exists():
        return Router(Config.load(toml), ledger=ledger), None, "configurado"
    key = vault.get_secret("anthropic")
    if not key:
        return Router(BASIC_CONFIG), None, "basico"
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
    claude = Router(DEFAULT_CONFIG, ledger=ledger, overrides={"claude": provider})
    return Router(BASIC_CONFIG), claude, "basico + claude"


def _who(reader: str, done) -> str:
    """Qué lector leyó este estudio, para avisar a la persona ('basico', 'claude' o el configurado)."""
    if reader == "basico + claude":
        return "claude" if done.used_fallback else "basico"
    return reader


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
    backup_scheduler: bool = False,
) -> FastAPI:
    data_dir = data_dir or store.default_data_dir()
    db = store.connect(data_dir)
    db.executescript(ingest.SCHEMA)
    ingest.migrate_imaging(db)
    ingest.migrate_observation(db)
    ingest.migrate_document(db)
    ingest.load_custom(db)
    db.executescript(backup.SETTINGS_SCHEMA)
    clinical.migrate(db)
    ingest.repair_references(db)
    fixed_router = router
    vault = Vault(data_dir / "originals", key_provider)
    app = FastAPI(title="House", docs_url=None, redoc_url=None, openapi_url=None)
    # Rechaza peticiones con otro Host (defensa contra DNS rebinding desde páginas externas).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.db, app.state.vault = db, vault

    def current_router() -> tuple[Router, Router | None, str]:
        return (fixed_router, None, "prueba") if fixed_router else reader_router(data_dir, vault)

    @app.middleware("http")
    async def _no_cross_site_writes(request: Request, call_next):
        # Las escrituras deben venir de la propia app (cabecera que un formulario externo no puede poner).
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-house") != "1":
            return Response("Petición no permitida", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def _always_fresh_pages(request: Request, call_next):
        # La pantalla (HTML, JS, CSS) se revalida siempre: tras una actualización, el navegador no debe
        # seguir mostrando la versión anterior guardada.
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

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
            for table in ("document", "study_image")
            for r in db.execute(f"SELECT file_path FROM {table} WHERE person_id = ?", (person_id,))
        ]
        db.execute("DELETE FROM access_log WHERE subject_id = ? OR actor_id = ?", (person_id, person_id))
        db.execute("DELETE FROM person WHERE id = ?", (person_id,))
        for f in files:
            vault.delete(f)
        return {"ok": True}

    def load_observations(person_id: int) -> list[dict]:
        rows = db.execute(
            "SELECT o.analyte_key, o.printed_name, o.value_num, o.qualifier, o.value_text, o.unit, "
            "o.ref_low, o.ref_high, o.ref_printed, o.status, o.method, o.collected_on, o.document_id, "
            "o.entered_manually, "
            "d.title AS document_title "
            "FROM observation o JOIN document d ON d.id = o.document_id WHERE o.person_id = ? "
            "ORDER BY o.collected_on, o.analyte_key",
            (person_id,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/people/{person_id}/observations")
    def observations(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_resultados")
        return load_observations(person_id)

    def assistant_router() -> Router | None:
        """El asistente conversa con Claude (o el proveedor configurado); el lector básico no puede."""
        router, fallback, label = current_router()
        if fallback is not None:
            return fallback
        return router if label in ("prueba", "configurado") else None

    @app.get("/api/assistant/status")
    def assistant_status(_: Me) -> dict:
        return {"available": assistant_router() is not None}

    @app.post("/api/people/{person_id}/assistant")
    def ask_assistant(person_id: int, body: AssistantQuestion, actor: Me) -> dict:
        """Pregunta a tu expediente: responde con lo que hay guardado y cita el documento de origen."""
        person = subject(person_id, actor, "consultar_asistente")
        router = assistant_router()
        if router is None:
            raise HTTPException(409, "El asistente necesita Claude: conecta tu clave en Configuración.")
        try:
            return assistant.ask(
                router,
                db,
                person,
                ingest.person_names(db, person),
                [m.model_dump() for m in body.messages],
            )
        except assistant.AssistantError as e:
            raise HTTPException(e.status, e.message) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo consultar a Claude: {e}") from None

    def profile_of(person: sqlite3.Row) -> dict:
        return {"sex": person["sex_at_birth"], "birth_date": person["birth_date"]}

    @app.get("/api/people/{person_id}/overview")
    def overview(person_id: int, actor: Me) -> dict:
        """Resultados y resumen (vigente vs historial) en una sola consulta: un solo registro de acceso.

        Los resultados sin rango del laboratorio reciben el del estudio anterior o, si no hay, uno general
        por sexo y edad (`ref_source` dice cuál); nada de eso se guarda en la base.
        """
        person = subject(person_id, actor, "ver_resultados")
        profile = profile_of(person)
        obs = summary_mod.apply_references(load_observations(person_id), profile)
        return {"observations": obs, "summary": summary_mod.summarize(obs, profile=profile)}

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
        if ingest.image_type(data):
            try:
                image_id = ingest.ingest_image(db, vault, person, file.filename or "Imagen", data)
            except ingest.IngestError as e:
                raise HTTPException(e.status, {"message": e.message, "document_id": None}) from None
            return {"image_id": image_id, "rows": 0, "kind": "foto"}
        router, fallback, reader = current_router()
        try:
            done = ingest.ingest_pdf(
                db, vault, router, person, file.filename or "Estudio.pdf", data, fallback=fallback
            )
        except ingest.IngestError as e:
            raise HTTPException(e.status, {"message": e.message, "document_id": e.document_id}) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            # El mensaje del proveedor no incluye contenido del documento (ver providers/base.py).
            raise HTTPException(502, f"No se pudo leer con la IA: {e}") from None
        return {
            "document_id": done.document_id,
            "rows": done.rows,
            "kind": done.kind,
            "reader": _who(reader, done),
        }

    @app.post("/api/people/{person_id}/prescriptions")
    async def upload_prescription(person_id: int, actor: Me, file: Annotated[UploadFile, File()]) -> dict:
        """Receta en foto o PDF: Claude propone los medicamentos y tú los revisas (se envía la imagen)."""
        person = subject(person_id, actor, "subir_estudio")
        data = await file.read(ingest.MAX_BYTES + 1)
        router = assistant_router()
        if router is None:
            raise HTTPException(409, "Leer recetas necesita Claude: conecta tu clave en Configuración.")
        try:
            done = ingest.ingest_prescription(db, vault, router, person, file.filename or "Receta", data)
        except ingest.IngestError as e:
            raise HTTPException(e.status, {"message": e.message, "document_id": e.document_id}) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo leer con Claude: {e}") from None
        return {"document_id": done.document_id, "medications": done.rows, "kind": "receta"}

    @app.post("/api/documents/{doc_id}/review-prescription")
    def review_prescription(doc_id: int, body: RxReview, actor: Me) -> dict:
        doc = document(doc_id, actor, "revisar_estudio")
        try:
            saved = ingest.confirm_prescription(db, doc_id, doc["person_id"], body.model_dump())
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return {"saved": saved}

    @app.get("/api/people/{person_id}/documents")
    def list_documents(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_estudios")
        rows = db.execute(
            "SELECT d.id, d.doc_type, d.title, d.filename, d.collected_on, d.review_state, d.uploaded_at, "
            "(SELECT COUNT(*) FROM observation o WHERE o.document_id = d.id) "
            "+ (SELECT COUNT(*) FROM imaging_study i WHERE i.document_id = d.id) "
            "+ (SELECT COUNT(*) FROM medication m WHERE m.document_id = d.id) AS results "
            "FROM document d WHERE person_id = ? ORDER BY COALESCE(collected_on, uploaded_at) DESC",
            (person_id,),
        )
        docs = [dict(r) for r in rows]
        for d in docs:
            if d["doc_type"] == "laboratorio" and d["review_state"] != "descartada":
                d["not_saved"] = ingest.not_saved_rows(db, d["id"], d["review_state"] == "revisada")
            elif d["doc_type"] == "imagen" and d["review_state"] != "descartada":
                d["gaps"] = ingest.imaging_gaps(db, d["id"], d["review_state"] == "revisada")
        return docs

    @app.get("/api/documents/{doc_id}")
    def get_document(doc_id: int, actor: Me) -> dict:
        document(doc_id, actor, "ver_estudio")
        return ingest.review_payload(db, doc_id)

    @app.get("/api/documents/{doc_id}/file")
    def get_file(doc_id: int, actor: Me) -> Response:
        doc = document(doc_id, actor, "ver_original")
        data = vault.get(doc["file_path"])
        return Response(
            data,
            media_type=ingest.image_type(data) or "application/pdf",  # las recetas también pueden ser fotos
            headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
        )

    @app.post("/api/documents/{doc_id}/reread")
    def reread(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "releer_estudio")
        person = db.execute("SELECT * FROM person WHERE id = ?", (doc["person_id"],)).fetchone()
        router, fallback, reader = current_router()
        try:
            done = ingest.reread(db, vault, router, doc_id, person, fallback=fallback)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo leer con la IA: {e}") from None
        return {"document_id": doc_id, "rows": done.rows, "kind": done.kind, "reader": _who(reader, done)}

    @app.get("/api/documents/{doc_id}/layout")
    def get_layout(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "ver_original")
        if doc["doc_type"] == "receta":
            return {"pages": [], "boxes": {}}  # una foto no tiene renglones que resaltar
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

    @app.post("/api/documents/{doc_id}/complete-read")
    def complete_read(doc_id: int, actor: Me) -> dict:
        """Estudio ya revisado: lo vuelve a leer y deja abierto solo lo que no está guardado."""
        doc = document(doc_id, actor, "releer_estudio")
        person = db.execute("SELECT * FROM person WHERE id = ?", (doc["person_id"],)).fetchone()
        router, fallback, _ = current_router()
        try:
            left = ingest.refresh_missing(db, vault, router, doc_id, person, fallback=fallback)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo leer con la IA: {e}") from None
        return {"document_id": doc_id, "open": left}

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
                [m.model_dump() for m in body.manual],
                complete=body.complete,
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

    def clinical_call(fn, *args):
        try:
            return fn(*args)
        except clinical.ClinicalError as e:
            raise HTTPException(e.status, e.message) from None

    @app.get("/api/clinical/suggestions")
    def clinical_suggestions(_: Me) -> dict:
        return clinical.SUGGESTIONS

    @app.get("/api/people/{person_id}/clinical")
    def clinical_overview(person_id: int, actor: Me) -> dict:
        subject(person_id, actor, "ver_expediente")
        return clinical.overview(db, person_id)

    class LinkBody(BaseModel):
        kind: str
        ref: str

    class Ingredient(BaseModel):
        active_ingredient: str

    @app.post("/api/people/{person_id}/medications/suggest-ingredients")
    def suggest_ingredients(person_id: int, actor: Me) -> dict:
        """Sugerencias de sustancia activa para los medicamentos que no la tienen (no se guarda nada)."""
        subject(person_id, actor, "editar_expediente")
        router = assistant_router()
        if router is None:
            raise HTTPException(
                409, "Sugerir sustancias activas necesita Claude: conecta tu clave en Configuración."
            )
        rows = db.execute(
            "SELECT id, name, brand FROM medication "
            "WHERE person_id = ? AND COALESCE(active_ingredient, '') = ''",
            (person_id,),
        ).fetchall()
        items = [{"id": r["id"], "name": r["brand"] or r["name"]} for r in rows]
        try:
            found = drugs.suggest(router, items)
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo consultar a Claude: {e}") from None
        return {
            "suggestions": [
                {"id": i["id"], "name": i["name"], "active_ingredient": found[i["id"]]}
                for i in items
                if i["id"] in found
            ]
        }

    @app.put("/api/people/{person_id}/medications/{med_id}/ingredient")
    def set_ingredient(person_id: int, med_id: int, body: Ingredient, actor: Me) -> dict:
        """Acepta una sustancia activa: el nombre que había pasa a ser el nombre comercial si era otro."""
        subject(person_id, actor, "editar_expediente")
        m = db.execute(
            "SELECT * FROM medication WHERE id = ? AND person_id = ?", (med_id, person_id)
        ).fetchone()
        if m is None:
            raise HTTPException(404, "No encontré ese medicamento en este perfil.")
        data = dict(m)
        ingredient = " ".join(body.active_ingredient.split())
        if not data.get("brand") and clinical._plain(m["name"]) != clinical._plain(ingredient):
            data["brand"] = m["name"]
        data["active_ingredient"] = ingredient
        clinical_call(clinical.update, db, person_id, "medication", med_id, data)
        return {"ok": True}

    @app.get("/api/people/{person_id}/link-candidates")
    def link_candidates(person_id: int, actor: Me) -> dict:
        subject(person_id, actor, "ver_expediente")
        return clinical.link_candidates(db, person_id)

    @app.post("/api/people/{person_id}/clinical/problem/{problem_id}/links")
    def add_problem_link(person_id: int, problem_id: int, body: LinkBody, actor: Me) -> dict:
        subject(person_id, actor, "editar_expediente")
        return {"id": clinical_call(clinical.add_link, db, person_id, problem_id, body.kind, body.ref)}

    @app.delete("/api/people/{person_id}/clinical/problem/{problem_id}/links/{link_id}")
    def remove_problem_link(person_id: int, problem_id: int, link_id: int, actor: Me) -> dict:
        subject(person_id, actor, "editar_expediente")
        clinical_call(clinical.remove_link, db, person_id, problem_id, link_id)
        return {"ok": True}

    @app.post("/api/people/{person_id}/clinical/{kind}")
    def clinical_add(person_id: int, kind: str, actor: Me, data: Annotated[dict, Body()]) -> dict:
        subject(person_id, actor, "editar_expediente")
        return {"id": clinical_call(clinical.add, db, person_id, kind, data)}

    @app.put("/api/people/{person_id}/clinical/{kind}/{item_id}")
    def clinical_update(
        person_id: int, kind: str, item_id: int, actor: Me, data: Annotated[dict, Body()]
    ) -> dict:
        subject(person_id, actor, "editar_expediente")
        clinical_call(clinical.update, db, person_id, kind, item_id, data)
        return {"ok": True}

    @app.delete("/api/people/{person_id}/clinical/{kind}/{item_id}")
    def clinical_delete(person_id: int, kind: str, item_id: int, actor: Me) -> dict:
        subject(person_id, actor, "editar_expediente")
        clinical_call(clinical.remove, db, person_id, kind, item_id)
        return {"ok": True}

    @app.put("/api/people/{person_id}/clinical-none/{section}")
    def clinical_none(person_id: int, section: str, actor: Me) -> dict:
        """Confirma que no hay datos en la sección (p. ej. sin alergias conocidas)."""
        subject(person_id, actor, "editar_expediente")
        clinical_call(clinical.set_none, db, person_id, section, True)
        return {"ok": True}

    @app.delete("/api/people/{person_id}/clinical-none/{section}")
    def clinical_none_off(person_id: int, section: str, actor: Me) -> dict:
        subject(person_id, actor, "editar_expediente")
        clinical_call(clinical.set_none, db, person_id, section, False)
        return {"ok": True}

    @app.get("/api/people/{person_id}/imaging")
    def imaging_list(person_id: int, actor: Me) -> list[dict]:
        subject(person_id, actor, "ver_imagen")
        rows = db.execute(
            "SELECT i.id, i.document_id, i.modality, i.study_name, i.performed_on, i.technique, "
            "i.indication, i.findings, i.prior, i.conclusion, i.suggestions, i.radiologist, i.site, i.flag, "
            "i.tables_json, d.title AS document_title, d.filename FROM imaging_study i "
            "LEFT JOIN document d ON d.id = i.document_id WHERE i.person_id = ? "
            "ORDER BY i.performed_on DESC, i.id",
            (person_id,),
        )
        studies = [dict(r) for r in rows]
        for s in studies:
            s["tables"] = json.loads(s.pop("tables_json")) if s.get("tables_json") else None
        linked, _ = ingest.link_images(person_images(person_id), studies)
        manual = {
            (r["a"], r["b"])
            for r in db.execute(
                "SELECT a, b FROM study_link WHERE a IN (SELECT id FROM imaging_study WHERE person_id = ?)",
                (person_id,),
            )
        }
        related = ingest.related_studies(studies, manual)
        problems = clinical.problems_of(db, person_id)
        return [
            {
                **s,
                "images": linked.get(s["id"], []),
                "related": related[s["id"]],
                "problems": problems.get(("imaging", str(s["id"])), []),
            }
            for s in studies
        ]

    class StudyLink(BaseModel):
        other_id: int

    def study_pair(study_id: int, other_id: int, actor: sqlite3.Row) -> tuple[int, int]:
        rows = db.execute(
            "SELECT id, person_id FROM imaging_study WHERE id IN (?, ?)", (study_id, other_id)
        ).fetchall()
        if study_id == other_id or len(rows) != 2 or rows[0]["person_id"] != rows[1]["person_id"]:
            raise HTTPException(422, "Esos dos estudios no se pueden relacionar.")
        subject(rows[0]["person_id"], actor, "subir_estudio")
        return min(study_id, other_id), max(study_id, other_id)

    @app.post("/api/imaging/{study_id}/links")
    def add_study_link(study_id: int, body: StudyLink, actor: Me) -> dict:
        a, b = study_pair(study_id, body.other_id, actor)
        db.execute("INSERT OR IGNORE INTO study_link(a, b) VALUES(?, ?)", (a, b))
        return {"ok": True}

    @app.delete("/api/imaging/{study_id}/links/{other_id}")
    def remove_study_link(study_id: int, other_id: int, actor: Me) -> dict:
        a, b = study_pair(study_id, other_id, actor)
        db.execute("DELETE FROM study_link WHERE a = ? AND b = ?", (a, b))
        return {"ok": True}

    def person_images(person_id: int) -> list[dict]:
        rows = db.execute(
            "SELECT id, title, performed_on FROM study_image WHERE person_id = ? ORDER BY title", (person_id,)
        )
        return [dict(r) for r in rows]

    @app.post("/api/people/{person_id}/imaging/refresh")
    def refresh_imaging(person_id: int, actor: Me) -> dict:
        """Vuelve a leer los informes confirmados con la versión actual: solo textos, no lo confirmado."""
        subject(person_id, actor, "releer_estudio")
        return ingest.refresh_imaging_text(db, vault, person_id)

    class Rename(BaseModel):
        name: str

    @app.put("/api/documents/{doc_id}/title")
    def rename_doc(doc_id: int, body: Rename, actor: Me) -> dict:
        """Nombre corregido del documento; el nombre del archivo original se conserva."""
        document(doc_id, actor, "revisar_estudio")
        try:
            ingest.rename_document(db, doc_id, body.name)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return {"ok": True}

    @app.put("/api/imaging/{study_id}/name")
    def rename_imaging(study_id: int, body: Rename, actor: Me) -> dict:
        owner = db.execute("SELECT person_id FROM imaging_study WHERE id = ?", (study_id,)).fetchone()
        if owner is None:
            raise HTTPException(404, "Informe no encontrado")
        subject(owner["person_id"], actor, "revisar_estudio")
        try:
            ingest.rename_study(db, study_id, body.name)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return {"ok": True}

    @app.post("/api/imaging/{study_id}/read-tables")
    def read_tables(study_id: int, actor: Me) -> dict:
        """Transcribe con Claude las tablas de un informe escaneado. Envía las imágenes del informe."""
        owner = db.execute("SELECT person_id FROM imaging_study WHERE id = ?", (study_id,)).fetchone()
        if owner is None:
            raise HTTPException(404, "Informe no encontrado")
        subject(owner["person_id"], actor, "releer_estudio")
        router = assistant_router()
        if router is None:
            raise HTTPException(409, "Leer tablas necesita Claude: conecta tu clave en Configuración.")
        try:
            return ingest.read_study_tables(db, vault, router, study_id)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        except BudgetExceeded:
            raise HTTPException(402, "Se alcanzó el tope mensual de gasto en IA.") from None
        except ProviderError as e:
            raise HTTPException(502, f"No se pudo leer con Claude: {e}") from None

    @app.get("/api/people/{person_id}/images/loose")
    def loose_images(person_id: int, actor: Me) -> list[dict]:
        """Imágenes que no coinciden con ningún informe (para que no queden escondidas)."""
        subject(person_id, actor, "ver_imagen")
        studies = [
            dict(r)
            for r in db.execute(
                "SELECT id, study_name, modality, performed_on FROM imaging_study WHERE person_id = ?",
                (person_id,),
            )
        ]
        return ingest.link_images(person_images(person_id), studies)[1]

    def image_row(image_id: int, actor: sqlite3.Row, action: str) -> sqlite3.Row:
        img = db.execute("SELECT * FROM study_image WHERE id = ?", (image_id,)).fetchone()
        if img is None:
            raise HTTPException(404, "Imagen no encontrada")
        subject(img["person_id"], actor, action)
        return img

    @app.get("/api/images/{image_id}/file")
    def image_file(image_id: int, actor: Me) -> Response:
        img = image_row(image_id, actor, "ver_imagen")
        return Response(
            vault.get(img["file_path"]), media_type=img["media_type"], headers={"Cache-Control": "no-store"}
        )

    @app.delete("/api/images/{image_id}")
    def delete_image(image_id: int, actor: Me) -> dict:
        img = image_row(image_id, actor, "borrar_estudio")
        db.execute("DELETE FROM study_image WHERE id = ?", (image_id,))
        vault.delete(img["file_path"])
        return {"ok": True}

    @app.delete("/api/documents/{doc_id}")
    def delete_document(doc_id: int, actor: Me) -> dict:
        doc = document(doc_id, actor, "borrar_estudio")
        db.execute("DELETE FROM imaging_study WHERE document_id = ?", (doc_id,))  # sin restos huérfanos
        db.execute(
            "UPDATE medication SET document_id = NULL WHERE document_id = ?", (doc_id,)
        )  # se conservan
        db.execute("DELETE FROM document WHERE id = ?", (doc_id,))
        vault.delete(doc["file_path"])
        return {"ok": True}

    @app.get("/api/catalog")
    def catalog() -> dict:
        return {
            a.key: {
                "name": a.name,
                "unit": a.unit,
                "group": a.group,
                "kind": a.kind,
                "about": explanations.about(a.key),
                "custom": a.key.startswith(terminology.CUSTOM_PREFIX),
            }
            for a in terminology.BY_KEY.values()
        }

    class CustomAnalyte(BaseModel):
        name: str
        unit: str = ""
        kind: str = "num"
        alias: str | None = None

    @app.post("/api/catalog/custom")
    def create_custom_analyte(body: CustomAnalyte, actor: Me) -> dict:
        """Análisis que el catálogo no trae: la persona lo crea y aparece en el menú con su gráfica."""
        try:
            a = ingest.create_custom(db, body.name, body.unit, body.kind, body.alias)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message) from None
        return {
            "key": a.key,
            "info": {
                "name": a.name,
                "unit": a.unit,
                "group": a.group,
                "kind": a.kind,
                "about": None,
                "custom": True,
            },
        }

    @app.get("/api/settings")
    def settings(_: Admin) -> dict:
        _, _, reader = current_router()
        month = db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS usd, COUNT(*) AS n FROM ai_call "
            "WHERE strftime('%Y-%m', at) = strftime('%Y-%m', 'now')"
        ).fetchone()
        return {
            "reader": "claude" if reader == "basico + claude" else reader,  # con clave: Claude de respaldo
            "budget_usd": DEFAULT_CONFIG.monthly_budget_usd,
            "month_usd": round(month["usd"], 4),
            "month_calls": month["n"],
            "data_dir": str(data_dir),
        }

    class BackupDestination(BaseModel):
        destination: str

    class BackupVerify(BaseModel):
        recovery_key: str

    def backup_call(fn, *args):
        try:
            return fn(*args)
        except backup.BackupError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/backup")
    def backup_status(_: Admin) -> dict:
        return {**backup.status(db), "suggestions": backup.suggested_destinations()}

    @app.post("/api/backup/setup")
    def backup_setup(body: BackupDestination, _: Admin, resp: Response) -> dict:
        """Activa el respaldo. La llave de recuperación se entrega UNA vez y House no la guarda."""
        if backup.status(db)["configured"]:
            raise HTTPException(
                409, "El respaldo ya está activado. Desactívalo primero para crear otra llave."
            )
        dest = backup_call(backup.check_destination, body.destination, data_dir)
        key, pub = backup.new_recovery_key()
        backup.put_settings(
            db,
            backup_dest=str(dest),
            backup_pub=pub,
            backup_last_at="",
            backup_last_file="",
            backup_last_size="",
            backup_last_error="",
        )
        resp.headers["Cache-Control"] = "no-store"
        return {"recovery_key": key, "destination": str(dest)}

    @app.put("/api/backup/destination")
    def backup_destination(body: BackupDestination, _: Admin) -> dict:
        if not backup.status(db)["configured"]:
            raise HTTPException(409, "Activa primero el respaldo.")
        dest = backup_call(backup.check_destination, body.destination, data_dir)
        backup.put_settings(db, backup_dest=str(dest))
        return backup.status(db)

    @app.post("/api/backup/run")
    def backup_run(_: Admin) -> dict:
        return backup_call(backup.run, db, data_dir, vault.master_key())

    @app.post("/api/backup/verify")
    def backup_verify(body: BackupVerify, _: Admin) -> dict:
        st = backup.status(db)
        if not st["last_file"]:
            raise HTTPException(409, "Todavía no hay ningún respaldo que verificar.")
        path = Path(st["destination"]) / st["last_file"]
        if not path.exists():
            raise HTTPException(404, "No encuentro el último respaldo en la carpeta de destino.")
        return backup_call(backup.verify_backup, path, body.recovery_key)

    @app.delete("/api/backup")
    def backup_off(_: Admin) -> dict:
        """Desactiva el respaldo automático. Los archivos ya creados se conservan."""
        backup.put_settings(db, backup_dest="", backup_pub="")
        return backup.status(db)

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

    if backup_scheduler:

        def _auto_backup() -> None:
            time.sleep(30)  # deja que House termine de abrir
            while True:
                if backup.status(db)["due"]:
                    backup.maybe_run(db, data_dir, vault.master_key())
                time.sleep(1800)

        threading.Thread(target=_auto_backup, daemon=True, name="house-backup").start()

    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app
