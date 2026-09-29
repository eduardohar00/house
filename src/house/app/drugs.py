"""Sustancia activa de un medicamento a partir de su nombre comercial (sugerencia que la persona confirma).

Claude solo recibe los nombres (no datos personales ni de salud) y responde únicamente cuando está seguro.
Nada se guarda hasta que la persona acepta cada sugerencia; es una ayuda, no información médica verificada.
"""

from __future__ import annotations

from ..providers import LLMRequest, Router

SYSTEM = """Recibes nombres de medicamentos, casi siempre nombres comerciales usados en México. Para cada uno
devuelve su sustancia activa (nombre genérico, en español, en minúsculas) SOLO si estás seguro.
Si el nombre no es claro, corresponde a productos con distinta sustancia según la presentación, o no lo
conoces, devuelve null. Si el nombre ya es una sustancia activa, devuélvela tal cual. Si el producto trae
varias sustancias, sepáralas con « + ». No des dosis, indicaciones ni advertencias.
Responde solo con el JSON pedido."""


def schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "active_ingredient": {"type": ["string", "null"]},
                    },
                    "required": ["id", "active_ingredient"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def suggest(router: Router, items: list[dict]) -> dict[int, str]:
    """{id del medicamento: sustancia activa sugerida}, solo de los que Claude conoce con seguridad."""
    if not items:
        return {}
    listing = "\n".join(f"{i['id']}: {i['name']}" for i in items)
    req = LLMRequest(
        task="interpret", system=SYSTEM, user=f"Medicamentos:\n{listing}", schema=schema(), max_tokens=2000
    )
    known = {i["id"] for i in items}
    out: dict[int, str] = {}
    for r in router.complete_json(req).data.get("items", []):
        value = " ".join(str(r.get("active_ingredient") or "").split())[:120]
        if r.get("id") in known and value:
            out[r["id"]] = value
    return out
