"""Adaptador de Gemini (google-genai). SIN VERIFICAR contra la API real: revisar antes de usarlo."""

from __future__ import annotations

import json
import time
from typing import Any

from .base import LLMRequest, LLMResponse, ProviderError, estimate_cost


class GeminiProvider:
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
            from google import genai

            client = genai.Client()
        self._client = client

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        from google.genai import types

        t0 = time.monotonic()
        try:
            resp = self._client.models.generate_content(
                model=self.model,
                contents=req.user,
                config=types.GenerateContentConfig(
                    system_instruction=req.system,
                    response_mime_type="application/json",
                    response_json_schema=req.schema,
                    max_output_tokens=req.max_tokens,
                ),
            )
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"{self.name}: {type(e).__name__}") from e
        latency = time.monotonic() - t0
        try:
            data = json.loads(resp.text)
        except (TypeError, json.JSONDecodeError) as e:
            raise ProviderError(f"{self.name}: JSON inválido") from e
        u = resp.usage_metadata
        i, o = (u.prompt_token_count or 0), (u.candidates_token_count or 0)
        return LLMResponse(
            data=data,
            provider=self.name,
            model=self.model,
            input_tokens=i,
            output_tokens=o,
            cost_usd=estimate_cost(i, o, self.input_per_mtok, self.output_per_mtok),
            latency_s=latency,
        )
