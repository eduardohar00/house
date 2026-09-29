"""Construye proveedores desde la configuración y controla el gasto mensual."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..config import Config, ProviderConfig
from .base import ChatResult, LLMRequest, LLMResponse, Provider, ProviderError
from .mock import BaselineRegexProvider


class BudgetExceeded(ProviderError):
    pass


def build_provider(cfg: ProviderConfig) -> Provider:
    price = {"input_per_mtok": cfg.input_per_mtok, "output_per_mtok": cfg.output_per_mtok}
    if cfg.kind == "mock":
        return BaselineRegexProvider(cfg.name)
    if cfg.kind == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg.name, cfg.model, effort=cfg.effort, **price)
    if cfg.kind == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(cfg.name, cfg.model, **price)
    if cfg.kind == "gemini":
        from .gemini_provider import GeminiProvider

        return GeminiProvider(cfg.name, cfg.model, **price)
    raise ProviderError(f"Tipo de proveedor desconocido: {cfg.kind}")


class UsageLedger:
    """Bitácora JSONL de llamadas a IA. Guarda tokens y costo, nunca el contenido."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None

    def month_total(self, now: datetime | None = None) -> float:
        if not self.path or not self.path.exists():
            return 0.0
        month = (now or datetime.now(UTC)).strftime("%Y-%m")
        total = 0.0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec["ts"].startswith(month):
                total += rec.get("cost_usd") or 0.0
        return total

    def record(self, task: str, resp: LLMResponse) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "task": task,
            "provider": resp.provider,
            "model": resp.model,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "cost_usd": resp.cost_usd,
            "request_id": resp.request_id,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")


class Router:
    """Entrega el proveedor configurado para cada tarea y aplica el tope de gasto."""

    def __init__(
        self,
        cfg: Config,
        ledger: UsageLedger | None = None,
        overrides: dict[str, Provider] | None = None,
    ) -> None:
        self.cfg = cfg
        self.ledger = ledger or UsageLedger(None)
        self._cache: dict[str, Provider] = dict(overrides or {})

    def provider_for(self, task: str) -> Provider:
        name = self.cfg.tasks.get(task)
        if not name:
            raise ProviderError(f"No hay proveedor configurado para la tarea '{task}'")
        if name not in self._cache:
            self._cache[name] = build_provider(self.cfg.providers[name])
        return self._cache[name]

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        if self.ledger.month_total() >= self.cfg.monthly_budget_usd:
            raise BudgetExceeded(
                f"Tope mensual de {self.cfg.monthly_budget_usd} USD alcanzado; el documento queda en cola."
            )
        resp = self.provider_for(req.task).complete_json(req)
        self.ledger.record(req.task, resp)
        return resp

    def chat_with_tools(
        self, task: str, system: str, messages: list, tools: list, run_tool, **kw
    ) -> ChatResult:
        if self.ledger.month_total() >= self.cfg.monthly_budget_usd:
            raise BudgetExceeded(f"Tope mensual de {self.cfg.monthly_budget_usd} USD alcanzado.")
        provider = self.provider_for(task)
        if not hasattr(provider, "chat_with_tools"):
            raise ProviderError("Este lector no puede conversar.")
        res = provider.chat_with_tools(system, messages, tools, run_tool, **kw)
        self.ledger.record(
            task,
            LLMResponse(
                data={},
                provider=res.provider,
                model=res.model,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                cost_usd=res.cost_usd,
                request_id=res.request_id,
            ),
        )
        return res
