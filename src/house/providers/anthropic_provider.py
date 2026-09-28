"""Adaptador de Claude (SDK oficial de Anthropic).

Notas de diseño (según la documentación vigente del SDK):
- Salida estructurada con `output_config.format` (JSON Schema). No se usa `tool_choice` forzado.
- No se envía `thinking` (el modelo decide) ni `temperature`; el esfuerzo va en `output_config.effort`.
- Si `stop_reason == "refusal"` se lanza ProviderRefusal; nunca se reintenta con otro contenido.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import LLMRequest, LLMResponse, ProviderError, ProviderRefusal, estimate_cost


class AnthropicProvider:
    def __init__(
        self,
        name: str,
        model: str,
        *,
        effort: str | None = None,
        input_per_mtok: float | None = None,
        output_per_mtok: float | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.effort = effort
        self.input_per_mtok = input_per_mtok
        self.output_per_mtok = output_per_mtok
        if client is None:
            import anthropic  # import perezoso: el SDK es opcional

            client = anthropic.Anthropic()  # credenciales desde el entorno
        self._client = client

    def build_kwargs(self, req: LLMRequest) -> dict:
        output_config: dict = {"format": {"type": "json_schema", "schema": req.schema}}
        effort = req.effort or self.effort
        if effort:
            output_config["effort"] = effort
        return {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
            "output_config": output_config,
        }

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        kwargs = self.build_kwargs(req)
        t0 = time.monotonic()
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001 - se re-lanza tipado, sin el prompt
            raise ProviderError(f"{self.name}: {type(e).__name__}") from e
        latency = time.monotonic() - t0
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ProviderRefusal(f"{self.name}: solicitud rechazada por el proveedor")
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise ProviderError(f"{self.name}: respuesta truncada (max_tokens)")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise ProviderError(f"{self.name}: respuesta sin texto")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ProviderError(f"{self.name}: JSON inválido") from e
        usage = resp.usage
        i, o = usage.input_tokens, usage.output_tokens
        return LLMResponse(
            data=data,
            provider=self.name,
            model=self.model,
            input_tokens=i,
            output_tokens=o,
            cost_usd=estimate_cost(i, o, self.input_per_mtok, self.output_per_mtok),
            latency_s=latency,
            request_id=getattr(resp, "_request_id", None),
        )
