"""Uso: python -m house.bench --cases bench/cases [--config config/house.toml] [--providers a,b]"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import Config
from .report import markdown
from .runner import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="house.bench")
    ap.add_argument("--cases", type=Path, default=Path("bench/cases"))
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--providers", default="baseline-regex", help="nombres separados por coma")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    cfg = Config.load(args.config) if args.config else None
    report = markdown(run(args.cases, cfg, [p for p in args.providers.split(",") if p]))
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
