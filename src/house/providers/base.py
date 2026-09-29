"""Interfaz común de proveedores de IA. La app nunca importa un SDK directamente."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ProviderError(RuntimeError):
    """Fallo del proveedor (red, autenticación, rechazo). El mensaje no incluye el prompt."""


class ProviderRefusal(ProviderError):
    """El proveedor rechazó la solicitud (política de seguridad del modelo)."""


@dataclass(frozen=True)
class LLMRequest:
    task: str  # extract | interpret | verify
    system: str
    user: str
    schema: dict  # JSON Schema de la respuesta
    max_tokens: int = 8000
    effort: str | None = None  # low | medium | high (si el proveedor lo soporta)


@dataclass(frozen=True)
class LLMResponse:
    data: dict
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    request_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChatResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0
    request_id: str | None = None
    rounds: int = 1  # cuántas veces se consultó al modelo (cada uso de herramientas suma una)


@runtime_checkable
class Provider(Protocol):
    name: str

    def complete_json(self, req: LLMRequest) -> LLMResponse: ...


def estimate_cost(
    input_tokens: int, output_tokens: int, input_per_mtok: float | None, output_per_mtok: float | None
) -> float | None:
    if input_per_mtok is None or output_per_mtok is None:
        return None
    return (input_tokens * input_per_mtok + output_tokens * output_per_mtok) / 1_000_000
