"""Avisos al mostrar la evolución de un marcador."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def mixed_methods(methods: Iterable[str | None]) -> list[str]:
    """Métodos distintos entre los puntos de una serie (vacío si todos coinciden o no se conocen).

    Si hay más de uno, la gráfica debe avisar: el mismo laboratorio advierte que, por ejemplo, el
    antígeno carcinoembrionario o el prostático no se comparan entre métodos distintos.
    """
    seen: dict[str, str] = {}
    for m in methods:
        if m and m.strip():
            seen.setdefault(_norm(m), m.strip())
    return sorted(seen.values()) if len(seen) > 1 else []
