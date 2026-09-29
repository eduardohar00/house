"""Servicio de macOS: python -m house.app.service install | uninstall | restart | status"""

from __future__ import annotations

import sys

from . import launchd


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    actions = {
        "install": launchd.install,
        "uninstall": launchd.uninstall,
        "restart": launchd.restart,
        "status": launchd.status,
    }
    if cmd not in actions:
        sys.exit("Uso: python -m house.app.service install | uninstall | restart | status")
    try:
        print(actions[cmd]())
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
