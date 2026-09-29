"""Adaptador de Claude (SDK oficial de Anthropic).

Notas de diseño (según la documentación vigente del SDK):
- Salida estructurada con `output_config.format` (JSON Schema). No se usa `tool_choice` forzado.
- No se envía `thinking` (el modelo decide) ni `temperature`; el esfuerzo va en `output_config.effort`.
- Si `stop_reason == "refusal"` se lanza ProviderRefusal; nunca se reintenta con otro contenido.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

from .base import ChatResult, LLMRequest, LLMResponse, ProviderError, ProviderRefusal, estimate_cost


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

    def _why(self, e: Exception) -> str:
        """Motivo del fallo en palabras claras, sin el contenido enviado."""
        text = str(e).lower()
        if "credit balance" in text:
            return f"{self.name}: se acabó el saldo de tu cuenta de Anthropic (agrega crédito en Billing)"
        return f"{self.name}: {type(e).__name__}"

    @staticmethod
    def _content(req: LLMRequest) -> Any:
        if not req.images:
            return req.user
        blocks: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg" if img.startswith(b"\xff\xd8\xff") else "image/png",
                    "data": base64.b64encode(img).decode(),
                },
            }
            for img in req.images
        ]
        return [*blocks, {"type": "text", "text": req.user}]

    def build_kwargs(self, req: LLMRequest) -> dict:
        output_config: dict = {"format": {"type": "json_schema", "schema": req.schema}}
        effort = req.effort or self.effort
        if effort:
            output_config["effort"] = effort
        return {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": self._content(req)}],
            "output_config": output_config,
        }

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        kwargs = self.build_kwargs(req)
        t0 = time.monotonic()
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:  # noqa: BLE001 - se re-lanza tipado, sin el prompt
            raise ProviderError(self._why(e)) from e
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

    def chat_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        run_tool: Callable[[str, dict], Any],
        *,
        max_tokens: int = 2000,
        max_rounds: int = 8,
        effort: str | None = None,
        web_search: dict | None = None,
    ) -> ChatResult:
        """Conversación con herramientas: el modelo pide datos, `run_tool` los da y sigue hasta responder.

        `web_search` = {"allowed_domains": [...], "max_uses": n} activa la búsqueda web del propio Claude,
        limitada a esos sitios; las páginas que cita quedan en `web_sources`. Si la cuenta no la tiene
        habilitada, se reintenta sin ella y `web_note` lo dice.
        """
        history = list(messages)
        t0 = time.monotonic()
        tin = tout = 0
        request_id = None
        web: dict[str, str] = {}
        web_note = None
        use_web = web_search
        for rounds in range(1, max_rounds + 1):
            all_tools = [*tools, self._web_tool(use_web)] if use_web else tools
            kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": history,
                "tools": all_tools,
            }
            if effort or self.effort:
                kwargs["output_config"] = {"effort": effort or self.effort}
            try:
                resp = self._client.messages.create(**kwargs)
            except Exception as e:  # noqa: BLE001 - se re-lanza tipado, sin el contenido
                if (
                    use_web and "web_search" in str(e).lower()
                ):  # la cuenta no la tiene: seguir sin buscar en la web
                    use_web, web_note = None, "La búsqueda web no está disponible en tu cuenta de Anthropic."
                    continue
                raise ProviderError(self._why(e)) from e
            tin += resp.usage.input_tokens
            tout += resp.usage.output_tokens
            request_id = getattr(resp, "_request_id", None) or request_id
            self._collect_web(resp.content, web)
            stop = getattr(resp, "stop_reason", None)
            if stop == "refusal":
                raise ProviderRefusal(f"{self.name}: solicitud rechazada por el proveedor")
            if (
                stop == "pause_turn"
            ):  # la búsqueda web pidió más tiempo: se le devuelve su turno para que siga
                history.append({"role": "assistant", "content": resp.content})
                continue
            if stop == "tool_use":
                history.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if getattr(block, "type", "") == "tool_use":
                        try:
                            out = run_tool(block.name, dict(block.input))
                            results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": json.dumps(out, ensure_ascii=False),
                                }
                            )
                        except Exception as e:  # noqa: BLE001 - el modelo recibe el motivo y puede intentar otra cosa
                            results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": str(e),
                                    "is_error": True,
                                }
                            )
                history.append({"role": "user", "content": results})
                continue
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            if stop == "max_tokens":
                raise ProviderError(f"{self.name}: respuesta truncada (max_tokens)")
            return ChatResult(
                text=text,
                provider=self.name,
                model=self.model,
                input_tokens=tin,
                output_tokens=tout,
                cost_usd=estimate_cost(tin, tout, self.input_per_mtok, self.output_per_mtok),
                latency_s=time.monotonic() - t0,
                request_id=request_id,
                rounds=rounds,
                web_sources=tuple({"url": u, "title": t} for u, t in web.items()),
                web_note=web_note,
            )
        raise ProviderError(f"{self.name}: demasiadas consultas seguidas sin respuesta")

    @staticmethod
    def _web_tool(cfg: dict) -> dict:
        tool = {"type": "web_search_20250305", "name": "web_search", "max_uses": int(cfg.get("max_uses", 5))}
        if cfg.get("allowed_domains"):
            tool["allowed_domains"] = list(cfg["allowed_domains"])
        return tool

    @staticmethod
    def _collect_web(content: Any, into: dict[str, str]) -> None:
        """Páginas web que Claude citó (o, si no citó ninguna, las que consultó)."""
        cited: dict[str, str] = {}
        seen: dict[str, str] = {}
        for b in content:
            kind = getattr(b, "type", "")
            if kind == "text":
                for c in getattr(b, "citations", None) or []:
                    url = getattr(c, "url", None)
                    if url:
                        cited[url] = getattr(c, "title", None) or url
            elif kind == "web_search_tool_result":
                found = getattr(b, "content", None)
                for r in found if isinstance(found, list) else []:
                    url = getattr(r, "url", None)
                    if url:
                        seen[url] = getattr(r, "title", None) or url
        into.update(cited or seen)
