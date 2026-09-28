"""Lista solo los NOMBRES y UNIDADES de los renglones sin reconocer de un caso.

No imprime valores, fechas ni datos personales: sirve para ampliar el catálogo de analitos
compartiendo únicamente los nombres de los análisis.

Uso: python -m house.bench.names bench/private/<id>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ..providers.mock import _LINE


def names_and_units(lines: list[str]) -> list[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for line in lines:
        m = _LINE.match(line)
        if m:
            out.add((re.sub(r"\s+", " ", m["name"]).strip(), m["unit"]))
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(prog="house.bench.names")
    ap.add_argument("case", type=Path)
    args = ap.parse_args()
    exp = json.loads((args.case / "expected.json").read_text(encoding="utf-8"))
    pairs = names_and_units(exp.get("_lineas_sin_reconocer", []))
    print(f"{len(pairs)} análisis sin reconocer (nombre | unidad):")
    for name, unit in pairs:
        print(f"{name} | {unit}")


if __name__ == "__main__":
    main()
