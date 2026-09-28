"""Métricas del banco de pruebas de extracción."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extract import Outcome, Provenance


@dataclass
class CaseScore:
    case: str
    provider: str
    expected: int = 0
    found: int = 0
    value_ok: int = 0
    unit_ok: int = 0
    extra_rows: int = 0
    ungrounded: int = 0
    needs_review: int = 0
    pii_leaks: int = 0
    date_ok: bool | None = None
    cost_usd: float | None = None
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    missing: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.found / self.expected if self.expected else 1.0

    @property
    def value_accuracy(self) -> float:
        return self.value_ok / self.expected if self.expected else 1.0


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01, 0.005 * abs(b))


def score_case(case: str, provider: str, expected: dict, outcome: Outcome, forbidden: list[str]) -> CaseScore:
    s = CaseScore(case=case, provider=provider)
    exp = {r["key"]: r for r in expected["results"]}
    s.expected = len(exp)
    by_key = {r.key: r for r in outcome.rows if r.key}
    for key, e in exp.items():
        row = by_key.get(key)
        if row is None:
            s.missing.append(key)
            continue
        s.found += 1
        if row.value is not None and _close(row.value, e["value"]):
            s.value_ok += 1
        else:
            s.wrong.append(f"{key}: esperado {e['value']}, obtenido {row.value}")
        if row.unit == e["unit"]:
            s.unit_ok += 1
    s.extra_rows = sum(1 for r in outcome.rows if r.key and r.key not in exp) + sum(
        1 for r in outcome.rows if not r.key
    )
    s.ungrounded = sum(1 for r in outcome.rows if Provenance.NOT_GROUNDED in r.problems)
    s.needs_review = sum(1 for r in outcome.rows if r.needs_review)
    hay = outcome.sent_text.lower()
    s.pii_leaks = sum(1 for f in forbidden if f.lower() in hay)
    if expected.get("collected_on"):
        s.date_ok = outcome.collected_on == expected["collected_on"]
    s.cost_usd = outcome.llm.cost_usd
    s.latency_s = outcome.llm.latency_s
    s.input_tokens, s.output_tokens = outcome.llm.input_tokens, outcome.llm.output_tokens
    return s
