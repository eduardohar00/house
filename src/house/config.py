"""Configuración: qué proveedor hace cada tarea, y el tope de gasto mensual."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TASKS = ("extract", "interpret", "verify")


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str  # anthropic | openai | gemini | mock
    model: str = ""
    effort: str | None = None
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None


@dataclass(frozen=True)
class Config:
    monthly_budget_usd: float = 15.0
    tasks: dict[str, str] = field(default_factory=dict)  # tarea -> nombre de proveedor
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        providers = {
            name: ProviderConfig(name=name, **{k: v for k, v in body.items()})
            for name, body in raw.get("providers", {}).items()
        }
        tasks = dict(raw.get("tasks", {}))
        for task, prov in tasks.items():
            if task not in TASKS:
                raise ValueError(f"Tarea desconocida en la configuración: {task}")
            if prov not in providers:
                raise ValueError(f"La tarea '{task}' usa el proveedor '{prov}', que no está definido")
        return cls(
            monthly_budget_usd=float(raw.get("budget", {}).get("monthly_usd", 15.0)),
            tasks=tasks,
            providers=providers,
        )
