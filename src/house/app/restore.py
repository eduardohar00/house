"""Recupera un respaldo de House en una carpeta vacía (por ejemplo, en una Mac nueva).

Uso:
    python -m house.app.restore <archivo.housebak> --to <carpeta vacía>
    python -m house.app.restore <archivo.housebak> --verify-only

Pide la llave de recuperación (no queda en el historial del terminal).
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import backup
from .vault import install_keychain_key


def main() -> None:
    ap = argparse.ArgumentParser(prog="house.app.restore")
    ap.add_argument("archivo", type=Path)
    ap.add_argument("--to", type=Path, help="carpeta vacía donde recuperar los datos")
    ap.add_argument(
        "--verify-only", action="store_true", help="solo comprobar que el respaldo se abre y está sano"
    )
    args = ap.parse_args()
    if not args.verify_only and not args.to:
        sys.exit("Indica --to <carpeta vacía>, o usa --verify-only.")
    key = getpass.getpass("Llave de recuperación: ")
    try:
        if args.verify_only:
            info = backup.verify_backup(args.archivo, key)
            print(
                f"Respaldo sano. Creado el {info['created_at']}: {info['counts']}, "
                f"{info['originals']} originales."
            )
            return
        info = backup.restore_backup(args.archivo, key, args.to, install_keychain_key)
    except backup.BackupError as e:
        sys.exit(f"No se pudo: {e}")
    print(f"Recuperado en {args.to}: {info['counts']}, {info['originals']} originales.")
    print(f"Para abrirlo: HOUSE_DATA_DIR='{args.to}' python -m house.app")


if __name__ == "__main__":
    main()
