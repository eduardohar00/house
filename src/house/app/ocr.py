"""Lectura de escaneos y fotos de documentos con el OCR de macOS (Vision). Todo ocurre en la Mac.

El lector es un programa pequeño en Swift (ocr.swift) que se compila una sola vez, la primera vez que
llega un PDF sin texto, y se guarda junto a los datos. Requiere las herramientas de desarrollo de Apple
(`xcode-select --install`); si faltan, se le dice a la persona en lugar de fallar en silencio.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

SOURCE = Path(__file__).with_name("ocr.swift")
TIMEOUT = 120


class OcrUnavailable(Exception):
    """No se puede leer el escaneo; el mensaje explica por qué, sin contenido del documento."""


def _binary(work_dir: Path) -> Path:
    if (
        os.environ.get("HOUSE_OCR") == "off"
    ):  # pruebas y equipos sin herramientas: no compilar ni leer escaneos
        raise OcrUnavailable("La lectura de escaneos está desactivada en esta instalación.")
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:12]
    out = work_dir / "bin" / f"house-ocr-{digest}"
    if out.exists():
        return out
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise OcrUnavailable(
            "Este PDF es un escaneo y necesito el lector de texto de macOS, que requiere las herramientas de "
            "desarrollo de Apple. Instálalas con «xcode-select --install» y vuelve a subirlo."
        )
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = out.with_suffix(".building")
    done = subprocess.run([swiftc, "-O", str(SOURCE), "-o", str(tmp)], capture_output=True, timeout=600)
    if done.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise OcrUnavailable("No pude preparar el lector de escaneos en esta Mac.")
    tmp.replace(out)
    return out


def ocr_bytes(data: bytes, suffix: str, work_dir: Path) -> str:
    """Texto de un PDF escaneado o de una imagen (una página por bloque, separadas por saltos de página)."""
    binary = _binary(work_dir)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"documento{suffix}"
        src.write_bytes(data)
        try:
            done = subprocess.run([str(binary), str(src)], capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            raise OcrUnavailable("Leer el escaneo tardó demasiado.") from None
    if done.returncode != 0:
        raise OcrUnavailable("No pude leer el escaneo.")
    return done.stdout.decode("utf-8", "replace")
