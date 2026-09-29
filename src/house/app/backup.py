"""Respaldo cifrado de House: base de datos, originales y la llave que los abre.

Por qué lleva también la llave maestra: los PDF están cifrados con una llave que vive solo en el
llavero de esta Mac. Un respaldo de los archivos sin esa llave no serviría si la Mac falla.

Cifrado: el respaldo se cifra con la parte PÚBLICA de una llave de recuperación (X25519 + AES-GCM). La
parte privada es la "llave de recuperación" que se muestra UNA vez y House no guarda: con la Mac robada
no se puede abrir ningún respaldo, y sin la llave nadie (tampoco House) puede recuperarlo.

Formato: MAGIC | llave efímera (32) | sal (16) | trozos [largo(4) | AES-GCM(trozo de 1 MiB)]. Cada trozo
autentica su posición y si es el último, así que un archivo truncado o reordenado se detecta.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import struct
import tarfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from .vault import open_blob

MAGIC = b"HOUSEBK1"
CHUNK = 1024 * 1024
PATTERN = "House-respaldo-*.housebak"
KEEP = 10  # cuántos respaldos se conservan en la carpeta
DUE_HOURS = 24  # cada cuánto se respalda solo, mientras House esté abierto
OVERDUE_DAYS = 7  # a partir de aquí se avisa que el respaldo está atrasado
_INFO = b"house-backup-v1"
_LOCK = threading.Lock()


class BackupError(Exception):
    """Mensaje claro para la persona; nunca incluye contenido de los estudios."""


# ---------- Llave de recuperación ----------


def _b32_groups(raw: bytes) -> str:
    s = base64.b32encode(raw).decode().rstrip("=")
    return "-".join(s[i : i + 5] for i in range(0, len(s), 5))


def new_recovery_key() -> tuple[str, str]:
    """(llave de recuperación para la persona, parte pública para House en base64)."""
    priv = X25519PrivateKey.generate()
    raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b32_groups(raw + hashlib.sha256(raw).digest()[:2]), base64.b64encode(pub).decode()


def parse_recovery_key(text: str) -> X25519PrivateKey:
    s = re.sub(r"[\s-]", "", text or "").upper().translate(str.maketrans("018", "OIB"))
    try:
        body = base64.b32decode(s + "=" * (-len(s) % 8))
    except Exception:
        raise BackupError("La llave de recuperación no es válida.") from None
    if len(body) != 34 or hashlib.sha256(body[:32]).digest()[:2] != body[32:]:
        raise BackupError("La llave de recuperación tiene un error de captura. Revísala con cuidado.")
    return X25519PrivateKey.from_private_bytes(body[:32])


def _derive(shared: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO).derive(shared)


# ---------- Flujo cifrado por trozos ----------


def _nonce(n: int) -> bytes:
    return b"\x00\x00\x00\x00" + n.to_bytes(8, "big")


def _aad(n: int, final: bool) -> bytes:
    return n.to_bytes(8, "big") + (b"F" if final else b"N")


class _Writer:
    def __init__(self, out: BinaryIO, key: bytes) -> None:
        self.out, self.aes, self.buf, self.n = out, AESGCM(key), bytearray(), 0

    def _emit(self, plain: bytes, final: bool) -> None:
        ct = self.aes.encrypt(_nonce(self.n), plain, _aad(self.n, final))
        self.out.write(struct.pack(">I", len(ct)) + ct)
        self.n += 1

    def write(self, data: bytes) -> int:
        self.buf += data
        while len(self.buf) >= CHUNK:
            chunk = bytes(self.buf[:CHUNK])
            del self.buf[:CHUNK]
            self._emit(chunk, False)
        return len(data)

    def close(self) -> None:
        self._emit(bytes(self.buf), True)
        self.buf.clear()


class _Reader:
    def __init__(self, src: BinaryIO, key: bytes) -> None:
        self.src, self.aes, self.buf, self.n, self.done = src, AESGCM(key), bytearray(), 0, False

    def _next(self) -> None:
        head = self.src.read(4)
        if len(head) < 4:
            raise BackupError("El respaldo está incompleto (le falta el final).")
        (size,) = struct.unpack(">I", head)
        ct = self.src.read(size)
        if len(ct) < size:
            raise BackupError("El respaldo está incompleto (le falta el final).")
        try:
            plain = self.aes.decrypt(_nonce(self.n), ct, _aad(self.n, False))
        except InvalidTag:
            try:
                plain = self.aes.decrypt(_nonce(self.n), ct, _aad(self.n, True))
                self.done = True
            except InvalidTag:
                raise BackupError(
                    "La llave no corresponde a este respaldo, o el archivo está dañado."
                ) from None
        self.n += 1
        self.buf += plain

    def read(self, size: int = -1) -> bytes:
        while (size < 0 or len(self.buf) < size) and not self.done:
            self._next()
        if size < 0:
            size = len(self.buf)
        out = bytes(self.buf[:size])
        del self.buf[:size]
        return out

    def finish(self) -> None:
        """Lee hasta el último trozo autenticado: garantiza que el respaldo no está truncado."""
        while not self.done:
            self._next()
        if self.src.read(1):
            raise BackupError("El respaldo trae datos de más; no es confiable.")


def _open_reader(path: Path, priv: X25519PrivateKey) -> _Reader:
    f = path.open("rb")
    head = f.read(len(MAGIC) + 48)
    if len(head) < len(MAGIC) + 48 or not head.startswith(MAGIC):
        f.close()
        raise BackupError("Este archivo no es un respaldo de House.")
    eph, salt = head[len(MAGIC) : len(MAGIC) + 32], head[len(MAGIC) + 32 :]
    key = _derive(priv.exchange(X25519PublicKey.from_public_bytes(eph)), salt)
    return _Reader(f, key)


# ---------- Crear ----------


def _snapshot(db_path: Path) -> tuple[bytes, dict]:
    """Copia consistente de la base (aunque la app la esté usando) y cuántos datos trae."""
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(":memory:")
    try:
        src.backup(dst)
        counts = {
            t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - tablas fijas
            for t in ("person", "document", "observation", "imaging_study")
        }
        data = bytearray(dst.serialize())
        # La copia es completa y no necesita el archivo -wal: se marca como base normal (bytes 18-19 = 1).
        # Sin esto, una copia en modo WAL no se puede abrir en memoria para verificarla.
        data[18] = data[19] = 1
        return bytes(data), counts
    finally:
        src.close()
        dst.close()


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = len(data), 0o600, int(datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def create_backup(
    data_dir: Path, dest_dir: Path, public_key_b64: str, master_key: bytes, now: datetime | None = None
) -> Path:
    now = now or datetime.now()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"House-respaldo-{now:%Y-%m-%d-%H%M%S}.housebak"
    tmp = final.with_suffix(".housebak.partial")
    db_bytes, counts = _snapshot(data_dir / "house.db")
    originals = sorted((data_dir / "originals").glob("*.bin")) if (data_dir / "originals").exists() else []
    eph = X25519PrivateKey.generate()
    salt = secrets.token_bytes(16)
    key = _derive(eph.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))), salt)
    manifest = {
        "version": 1,
        "created_at": now.isoformat(timespec="seconds"),
        "counts": counts,
        "originals": len(originals),
    }
    try:
        with tmp.open("wb") as out:
            os.chmod(tmp, 0o600)
            out.write(MAGIC + eph.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) + salt)
            writer = _Writer(out, key)
            tar = tarfile.open(fileobj=writer, mode="w|")  # noqa: SIM115
            _add(tar, "manifest.json", json.dumps(manifest).encode())
            _add(tar, "master.key", master_key)
            _add(tar, "house.db", db_bytes)
            usage = data_dir / "ai_usage.jsonl"
            if usage.exists():
                _add(tar, "ai_usage.jsonl", usage.read_bytes())
            for p in originals:
                _add(tar, f"originals/{p.name}", p.read_bytes())
            tar.close()
            writer.close()
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return final


def prune(dest_dir: Path, keep: int = KEEP) -> int:
    files = sorted(dest_dir.glob(PATTERN))
    for old in files[:-keep] if keep else files:
        old.unlink(missing_ok=True)
    return max(0, len(files) - keep)


# ---------- Leer: verificar y recuperar ----------

_ALLOWED = re.compile(r"^(manifest\.json|master\.key|house\.db|ai_usage\.jsonl|originals/[0-9a-z-]+\.bin)$")


def _members(path: Path, recovery_key: str):
    """Recorre el respaldo (ya autenticado trozo a trozo) y devuelve (nombre, bytes)."""
    reader = _open_reader(path, parse_recovery_key(recovery_key))
    try:
        tar = tarfile.open(fileobj=reader, mode="r|")  # noqa: SIM115
        for member in tar:
            if not _ALLOWED.match(member.name) or not member.isfile():
                raise BackupError("El respaldo trae contenido inesperado; no es confiable.")
            f = tar.extractfile(member)
            yield member.name, (f.read() if f else b"")
        reader.finish()
    finally:
        reader.src.close()


def verify_backup(path: Path, recovery_key: str) -> dict:
    """Comprueba de punta a punta que el respaldo se puede abrir y sus datos están sanos."""
    manifest = master = None
    originals = 0
    db_ok = False
    for name, data in _members(path, recovery_key):
        if name == "manifest.json":
            manifest = json.loads(data)
        elif name == "master.key":
            master = data
        elif name == "house.db":
            mem = sqlite3.connect(":memory:")
            try:
                mem.deserialize(data)
                db_ok = mem.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                mem.close()
        elif name.startswith("originals/"):
            if master is None:
                raise BackupError("El respaldo no trae la llave de los originales.")
            try:
                open_blob(master, data)
            except Exception:
                raise BackupError("Un original del respaldo está dañado.") from None
            originals += 1
    if manifest is None or master is None or not db_ok:
        raise BackupError("El respaldo está incompleto o su base de datos está dañada.")
    if originals != manifest["originals"]:
        raise BackupError("Al respaldo le faltan originales.")
    return {"created_at": manifest["created_at"], "counts": manifest["counts"], "originals": originals}


def restore_backup(
    path: Path, recovery_key: str, target_dir: Path, install_key: Callable[[bytes], None]
) -> dict:
    """Recupera en una carpeta vacía (nunca sobreescribe datos existentes) e instala la llave maestra."""
    target_dir = Path(target_dir)
    if (target_dir / "house.db").exists():
        raise BackupError("Ya hay datos de House en esa carpeta; elige una carpeta vacía.")
    staging = target_dir / ".recuperando"
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "originals").mkdir(parents=True, mode=0o700)
    master = None
    seen: set[str] = set()
    try:
        for name, data in _members(path, recovery_key):
            seen.add(name)
            if name == "master.key":
                master = data
            elif name != "manifest.json":
                dest = staging / name
                dest.write_bytes(data)
                dest.chmod(0o600)
            else:
                (staging / "manifest.json").write_bytes(data)
        if master is None or "house.db" not in seen:
            raise BackupError("El respaldo está incompleto.")
        (target_dir / "originals").mkdir(exist_ok=True, mode=0o700)
        for p in (staging / "originals").iterdir():
            os.replace(p, target_dir / "originals" / p.name)
        for name in ("ai_usage.jsonl", "house.db"):
            if (staging / name).exists():
                os.replace(staging / name, target_dir / name)
        manifest = json.loads((staging / "manifest.json").read_text())
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    install_key(master)
    return manifest


# ---------- Estado y ejecución (lo que usa la app) ----------

SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def get_settings(db) -> dict[str, str]:
    return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM app_setting")}


def put_settings(db, **values: str) -> None:
    for k, v in values.items():
        db.execute(
            "INSERT INTO app_setting(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (k, v),
        )


def check_destination(dest: str, data_dir: Path) -> Path:
    if not dest or not dest.strip():
        raise BackupError("Indica la carpeta donde guardar los respaldos.")
    path = Path(dest.strip()).expanduser()
    if not path.is_absolute():
        raise BackupError("Escribe la ruta completa de la carpeta.")
    real, data = path.resolve(), data_dir.resolve()
    if real == data or data in real.parents or real in data.parents:
        raise BackupError(
            "Elige una carpeta fuera de los datos de House: si vive junto a ellos, no te protege."
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".house-{secrets.token_hex(4)}"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError:
        raise BackupError("No puedo escribir en esa carpeta. Revisa que exista y tengas permiso.") from None
    return path


def suggested_destinations() -> list[str]:
    """Carpetas en la nube o discos externos, si existen (nunca se inventa una ruta)."""
    home = Path.home()
    found = [str(p) for p in sorted((home / "Library" / "CloudStorage").glob("GoogleDrive-*/My Drive"))]
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.exists():
        found.append(str(icloud))
    found += [str(p) for p in sorted(Path("/Volumes").glob("*")) if p.is_dir() and p.name != "Macintosh HD"]
    return [f"{p}/House respaldo" for p in found]


def status(db, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    cfg = get_settings(db)
    configured = bool(cfg.get("backup_dest") and cfg.get("backup_pub"))
    last_at = cfg.get("backup_last_at") or None
    age = (now - datetime.fromisoformat(last_at)).total_seconds() / 86400 if last_at else None
    return {
        "configured": configured,
        "destination": cfg.get("backup_dest") or None,
        "last_at": last_at,
        "last_file": cfg.get("backup_last_file") or None,
        "last_size": int(cfg["backup_last_size"]) if cfg.get("backup_last_size") else None,
        "last_error": cfg.get("backup_last_error") or None,
        "age_days": None if age is None else round(age, 1),
        "overdue": configured and (age is None or age > OVERDUE_DAYS),
        "due": configured and (age is None or age * 24 >= DUE_HOURS),
        "keep": KEEP,
    }


def run(db, data_dir: Path, master_key: bytes, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    cfg = get_settings(db)
    if not (cfg.get("backup_dest") and cfg.get("backup_pub")):
        raise BackupError("El respaldo no está activado.")
    if not _LOCK.acquire(blocking=False):
        raise BackupError("Ya hay un respaldo en curso.")
    try:
        try:
            dest = check_destination(cfg["backup_dest"], data_dir)
            path = create_backup(data_dir, dest, cfg["backup_pub"], master_key, now)
            prune(dest)
            put_settings(
                db,
                backup_last_at=now.isoformat(timespec="seconds"),
                backup_last_file=path.name,
                backup_last_size=str(path.stat().st_size),
                backup_last_error="",
            )
        except BackupError as e:
            put_settings(db, backup_last_error=str(e))
            raise
        except Exception as e:  # noqa: BLE001 - la persona ve un mensaje claro, no un rastro técnico
            put_settings(db, backup_last_error="No se pudo completar el respaldo.")
            raise BackupError("No se pudo completar el respaldo.") from e
    finally:
        _LOCK.release()
    return status(db, now)


def maybe_run(db, data_dir: Path, master_key: bytes) -> bool:
    """Respalda si toca (cada DUE_HOURS). Devuelve si lo intentó; nunca lanza."""
    try:
        if status(db)["due"]:
            run(db, data_dir, master_key)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False
