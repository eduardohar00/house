"""Arranca House en esta Mac: python -m house.app  (abre http://127.0.0.1:8765)."""

from __future__ import annotations

import argparse
import webbrowser


def main() -> None:
    import uvicorn

    from .api import create_app

    ap = argparse.ArgumentParser(prog="house.app")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    # Solo 127.0.0.1: nadie más en la red puede conectarse.
    uvicorn.run(create_app(backup_scheduler=True), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
