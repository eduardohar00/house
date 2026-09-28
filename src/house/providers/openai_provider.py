"""Adaptador de OpenAI. SIN VERIFICAR contra la API real: revisar antes de usarlo.

Usa Chat Completions con `response_format` de tipo json_schema estricto.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import LLMRequest, LLMResponse, ProviderError, ProviderRefusal, estimate_cost


class OpenAIProvider:
    def __init__(
        self,
        name: str,
        model: str,
        *,
        input_per_mtok: float | None = None,
        output_per_mtok: float | None = None,
        client: Any | None = None,
        **_: Any,
    ) -> None:
        if not model:
            raise ProviderError(f"{name}: falta 'model' en la configuración")
        self.name, self.model = name, model
        self.input_per_mtok, self.output_per_mtok = input_per_mtok, output_per_mtok
        if client is None:
            import openai

            client = openai.OpenAI()
        self._client = client

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "house_output", "schema": req.schema, "strict": True},
                },
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name}: {type(e).__name__}") from e
        latency = time.monotonic() - t0
        msg = resp.choices[0].message
        if getattr(msg, "refusal", None):
            raise ProviderRefusal(f"{self.name}: solicitud rechazada por el proveedor")
        try:
            data = json.loads(msg.content)
        except (TypeError, json.JSONDecodeError) as e:
            raise ProviderError(f"{self.name}: JSON inválido") from e
        i, o = resp.usage.prompt_tokens, resp.usage.completion_tokens
        return LLMResponse(
            data=data,
            provider=self.name,
            model=self.model,
            input_tokens=i,
            output_tokens=o,
            cost_usd=estimate_cost(i, o, self.input_per_mtok, self.output_per_mtok),
            latency_s=latency,
        )
