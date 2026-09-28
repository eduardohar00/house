from __future__ import annotations

from collections import defaultdict

from .metrics import CaseScore


def markdown(scores: list[CaseScore]) -> str:
    by_provider: dict[str, list[CaseScore]] = defaultdict(list)
    for s in scores:
        by_provider[s.provider].append(s)
    lines = [
        "# Resultados del banco de pruebas de extracción",
        "",
        "| Proveedor | Casos | Valores correctos | Faltantes | Filas extra | Sin respaldo | A revisar "
        "| Fugas de datos personales | Costo (USD) | Latencia media (s) | Errores |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for prov, items in by_provider.items():
        ok = [s for s in items if not s.error]
        exp = sum(s.expected for s in items)
        cost = sum(s.cost_usd or 0 for s in ok)
        lat = sum(s.latency_s for s in ok) / len(ok) if ok else 0
        lines.append(
            f"| {prov} | {len(items)} | {sum(s.value_ok for s in ok)}/{exp} "
            f"({100 * sum(s.value_ok for s in ok) / exp:.1f} %) | {sum(len(s.missing) for s in ok)} | "
            f"{sum(s.extra_rows for s in ok)} | {sum(s.ungrounded for s in ok)} | "
            f"{sum(s.needs_review for s in ok)} | {sum(s.pii_leaks for s in ok)} | "
            f"{cost:.4f} | {lat:.2f} | {len(items) - len(ok)} |"
            if exp
            else f"| {prov} | {len(items)} | n/a | | | | | | | | |"
        )
    lines += ["", "## Detalle de errores", ""]
    any_detail = False
    for s in scores:
        detail = s.wrong + [f"falta {k}" for k in s.missing] + ([f"ERROR: {s.error}"] if s.error else [])
        if detail:
            any_detail = True
            lines.append(f"- **{s.provider} / {s.case}**: " + "; ".join(detail))
    if not any_detail:
        lines.append("Sin errores.")
    lines += [
        "",
        "Criterio de aceptación provisional para pasar a modo semiautomático: 100 % de valores "
        "correctos tras revisión humana, 0 fugas de datos personales y 0 filas sin respaldo "
        "entre las marcadas como confiables.",
    ]
    return "\n".join(lines) + "\n"
