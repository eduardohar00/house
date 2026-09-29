"""Métricas del banco de pruebas de extracción."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..extract import Outcome, Provenance, Row
from ..normalize.ranges import norm_text_result


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


def _value_ok(row: Row, expected: float | str) -> bool:
    if isinstance(expected, str):
        return row.value_label is not None and norm_text_result(row.value_label) == norm_text_result(expected)
    return row.value is not None and _close(row.value, expected)


def score_case(case: str, provider: str, expected: dict, outcome: Outcome, forbidden: list[str]) -> CaseScore:
    s = CaseScore(case=case, provider=provider)
    exp = {r["key"]: r for r in expected["results"]}
    s.expected = len(exp)
    by_key: dict[str, list[Row]] = {}
    for r in outcome.rows:
        if r.key:
            by_key.setdefault(r.key, []).append(r)
    for key, e in exp.items():
        candidates = by_key.get(key)
        if not candidates:
            s.missing.append(key)
            continue
        s.found += 1
        # Un análisis puede aparecer más de una vez (p. ej. glucosa en la química y en el HOMA-IR).
        row = next((c for c in candidates if _value_ok(c, e["value"])), candidates[0])
        if _value_ok(row, e["value"]):
            s.value_ok += 1
        else:
            got = row.value if row.value is not None else row.value_label
            s.wrong.append(f"{key}: esperado {e['value']}, obtenido {got}")
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
