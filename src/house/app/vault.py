"""Cifrado de los documentos originales (AES-256-GCM).

La llave maestra vive en el llavero de macOS (no en disco ni en el repositorio). Cada archivo se
cifra con un nonce propio; el nombre en disco es aleatorio para no revelar nada del documento.
"""

from __future__ import annotations

import base64
import secrets
import subprocess
from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SERVICE = "House"
_ACCOUNT = "master-key"
_MAGIC = b"HOUSE1"

KeyProvider = Callable[[], bytes]


def keychain_key() -> bytes:
    """Lee la llave del llavero de macOS; si no existe, la crea. Nunca se imprime."""
    found = subprocess.run(
        ["security", "find-generic-password", "-s", _SERVICE, "-a", _ACCOUNT, "-w"],
        capture_output=True,
        text=True,
    )
    if found.returncode == 0:
        return base64.b64decode(found.stdout.strip())
    key = AESGCM.generate_key(bit_length=256)
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s",
            _SERVICE,
            "-a",
            _ACCOUNT,
            "-w",
            base64.b64encode(key).decode(),
        ],
        check=True,
        capture_output=True,
    )
    return key


class Vault:
    def __init__(self, root: Path, key_provider: KeyProvider = keychain_key) -> None:
        self.root = root
        self._key_provider = key_provider
        self._aes: AESGCM | None = None

    def _cipher(self) -> AESGCM:
        if self._aes is None:
            self._aes = AESGCM(self._key_provider())
        return self._aes

    def put(self, data: bytes) -> str:
        """Guarda cifrado y devuelve el nombre relativo del archivo."""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = f"{secrets.token_hex(16)}.bin"
        nonce = secrets.token_bytes(12)
        path = self.root / name
        path.write_bytes(_MAGIC + nonce + self._cipher().encrypt(nonce, data, _MAGIC))
        path.chmod(0o600)
        return name

    def get(self, name: str) -> bytes:
        blob = (self.root / Path(name).name).read_bytes()
        if not blob.startswith(_MAGIC):
            raise ValueError("Archivo no reconocido")
        nonce, body = blob[len(_MAGIC) : len(_MAGIC) + 12], blob[len(_MAGIC) + 12 :]
        return self._cipher().decrypt(nonce, body, _MAGIC)

    def delete(self, name: str) -> None:
        (self.root / Path(name).name).unlink(missing_ok=True)
