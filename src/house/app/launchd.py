"""House como servicio de macOS que se levanta solo cuando alguien abre la dirección.

launchd (el administrador de servicios de macOS) reserva el puerto de House. House NO se ejecuta al iniciar
sesión ni queda encendido sin necesidad: la primera vez que se abre http://127.0.0.1:8765, launchd lo arranca
y le entrega la conexión; si House se cae o se apaga, la siguiente visita lo vuelve a levantar.
"""

from __future__ import annotations

import ctypes
import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.house.app"
SOCKET = "Listeners"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "House.log"


def build_plist(python: str, workdir: str, port: int, log: str) -> dict:
    """Definición del servicio. Sin RunAtLoad ni KeepAlive: solo arranca cuando llega una conexión."""
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "house.app", "--launchd"],
        "WorkingDirectory": workdir,
        "Sockets": {SOCKET: {"SockNodeName": "127.0.0.1", "SockServiceName": str(port)}},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ThrottleInterval": 5,
        "ProcessType": "Interactive",
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"},
    }


def activate_socket() -> int:
    """Descriptor del puerto que launchd reservó para House (API launch_activate_socket)."""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    fn = lib.launch_activate_socket
    fn.argtypes = [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
        ctypes.POINTER(ctypes.c_size_t),
    ]
    fn.restype = ctypes.c_int
    fds = ctypes.POINTER(ctypes.c_int)()
    count = ctypes.c_size_t(0)
    if fn(SOCKET.encode(), ctypes.byref(fds), ctypes.byref(count)) != 0 or count.value < 1:
        raise RuntimeError("launchd no entregó el puerto de House (¿se ejecutó fuera del servicio?).")
    fd = fds[0]
    lib.free(fds)
    return fd


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(port: int = 8765) -> str:
    python = str(Path(sys.executable))
    workdir = str(Path(__file__).resolve().parents[3])
    log_path().parent.mkdir(parents=True, exist_ok=True)
    data = build_plist(python, workdir, port, str(log_path()))
    plist_path().parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # si ya estaba, se reemplaza
    plist_path().write_bytes(plistlib.dumps(data))
    done = _launchctl("bootstrap", _domain(), str(plist_path()))
    if done.returncode != 0:
        raise RuntimeError(f"No pude activar el servicio: {done.stderr.strip() or done.stdout.strip()}")
    return f"Servicio instalado. House se abre solo en http://127.0.0.1:{port}. Registro: {log_path()}"


def uninstall() -> str:
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
    return "Servicio quitado. House ya no se levanta solo."


def restart() -> str:
    """Detiene House; se vuelve a levantar solo en la siguiente visita."""
    _launchctl("kill", "SIGTERM", f"{_domain()}/{LABEL}")
    return "House se detuvo; se levantará solo cuando abras la página."


def status() -> str:
    if not plist_path().exists():
        return "No instalado."
    done = _launchctl("print", f"{_domain()}/{LABEL}")
    if done.returncode != 0:
        return "Instalado, pero launchd no lo tiene activo (usa «install»)."
    lines = [ln.strip() for ln in done.stdout.splitlines()]

    def field(name: str) -> str | None:
        return next((ln.split("=", 1)[1].strip() for ln in lines if ln.startswith(f"{name} =")), None)

    pid = field("pid")
    return f"Instalado. Estado: {f'encendido (pid {pid})' if pid else 'esperando la primera visita'}."
