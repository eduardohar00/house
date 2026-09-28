import json
import re
from datetime import date
from pathlib import Path

import pytest

from house.bench.report import markdown
from house.bench.runner import run
from house.config import Config
from house.extract import Provenance, extract_document, process
from house.privacy import Anonymizer
from house.providers import (
    BudgetExceeded,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderRefusal,
    Router,
    UsageLedger,
)
from house.providers.anthropic_provider import AnthropicProvider
from house.schema import RawExtraction, RawRow, extraction_json_schema

CASES = Path("bench/cases")
DOC = (CASES / "example-synthetic/document.txt").read_text(encoding="utf-8")


def router_for(provider_name="baseline-regex", **kw):
    from house.config import ProviderConfig

    cfg = Config(
        tasks={"extract": provider_name},
        providers={provider_name: ProviderConfig(provider_name, "mock")},
        **kw,
    )
    return Router(cfg)


class IdealModel:
    """Simula un modelo que lee bien el documento sintético (para probar el resto del pipeline)."""

    name = "ideal"

    def complete_json(self, req):
        rows = []
        for line in req.user.splitlines():
            m = re.match(r"^(?P<n>\S.*?)\s{2,}(?P<v>\d+(?:\.\d+)?)\s+(?P<u>\S+)\s+(?P<r>.+)$", line)
            if m:
                rows.append(
                    {
                        "analyte_name": m["n"].strip(),
                        "value_text": m["v"],
                        "unit_text": m["u"],
                        "ref_text": m["r"].strip(),
                        "evidence": line.strip(),
                    }
                )
        return LLMResponse(
            data={
                "document_type": "laboratorio",
                "collected_on": "2026-09-22",
                "lab_name": None,
                "rows": rows,
            },
            provider="ideal",
            model="ideal",
            cost_usd=0.0,
        )


def ideal_router():
    from house.config import ProviderConfig

    cfg = Config(tasks={"extract": "ideal"}, providers={"ideal": ProviderConfig("ideal", "mock")})
    return Router(cfg, overrides={"ideal": IdealModel()})


def test_pipeline_normalizes_and_classifies():
    out = extract_document(
        DOC,
        ideal_router(),
        anonymizer=Anonymizer(["Eduardo Ejemplo García", "Ramos"]),
        reference_date=date(2026, 9, 22),
    )
    by = {r.key: r for r in out.rows if r.key}
    assert by["glucose"].value == 92 and by["glucose"].status == "ok"
    assert by["ldl"].value == 108 and by["ldl"].status == "high"
    assert by["vitamin_d"].value == pytest.approx(36.86, abs=0.01) and by["vitamin_d"].converted
    assert by["vitamin_d"].needs_review  # toda conversión de unidades se revisa
    assert out.collected_on == "2026-09-22"
    assert "EDUARDO" not in out.sent_text and "GAEE830412" not in out.sent_text


def test_baseline_reads_clean_labs_even_with_collapsed_spacing():
    squashed = re.sub(r"[ \t]+", " ", DOC)  # así suelen salir los PDF al extraer su texto
    for text in (DOC, squashed):
        out = extract_document(
            text,
            router_for(),
            anonymizer=Anonymizer(["Eduardo Ejemplo García"]),
            reference_date=date(2026, 9, 22),
        )
        keys = {r.key for r in out.rows if r.key}
        assert len(keys) == 9 and {"alt", "vitamin_d"} <= keys


def test_baseline_ignores_non_result_lines():
    text = "Folio: A-1 Fecha de toma: 22/09/2026\nDomicilio: Calle Ejemplo 123, Col. Centro\nNota: valores 5 veces al año"
    out = extract_document(text, router_for(), reference_date=date(2026, 9, 22))
    assert out.rows == []


def test_ungrounded_row_is_flagged():
    raw = RawExtraction(
        document_type="laboratorio",
        collected_on=None,
        lab_name=None,
        rows=[
            RawRow(
                analyte_name="Glucosa",
                value_text="105",
                unit_text="mg/dL",
                ref_text="70 - 99",
                evidence="Glucosa   105 mg/dL   70 - 99",
            )
        ],
    )
    rows = process(raw, "Glucosa   92 mg/dL   70 - 99")
    assert Provenance.NOT_GROUNDED in rows[0].problems


def test_implausible_unknown_and_unit_problems():
    def row(name, val, unit):
        line = f"{name}  {val} {unit}"
        return RawRow(analyte_name=name, value_text=val, unit_text=unit, ref_text=None, evidence=line)

    raw = RawExtraction(
        document_type="laboratorio",
        collected_on=None,
        lab_name=None,
        rows=[
            row("Glucosa", "9000", "mg/dL"),
            row("Tiroglobulina", "4.1", "ng/mL"),
            row("Glucosa", "90", "furlongs"),
        ],
    )
    text = "\n".join(r.evidence for r in raw.rows)
    a, b, c = process(raw, text)
    assert Provenance.IMPLAUSIBLE in a.problems
    assert Provenance.UNKNOWN_ANALYTE in b.problems
    assert Provenance.UNIT_PROBLEM in c.problems


def test_bench_scores_baseline_and_ideal():
    scores = run(CASES, None, ["baseline-regex", "ideal"], overrides={"ideal": IdealModel()})
    base, ideal = scores
    assert base.pii_leaks == ideal.pii_leaks == 0
    assert base.ungrounded == ideal.ungrounded == 0
    assert (base.value_ok, base.expected) == (9, 9) and not base.missing
    assert ideal.value_ok == ideal.expected == 9 and ideal.date_ok
    report = markdown(scores)
    assert "baseline-regex" in report and "ideal" in report


def test_config_validation(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[tasks]\nextract = "nope"\n[providers.a]\nkind = "mock"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        Config.load(p)
    p.write_text(
        '[budget]\nmonthly_usd = 5\n[tasks]\nextract = "a"\n[providers.a]\nkind = "mock"\n', encoding="utf-8"
    )
    cfg = Config.load(p)
    assert cfg.monthly_budget_usd == 5 and cfg.tasks["extract"] == "a"
    example = Config.load("config/house.example.toml")
    assert example.providers["claude"].model == "claude-opus-5-5"


def test_router_enforces_budget(tmp_path):
    from house.config import ProviderConfig

    ledger = UsageLedger(tmp_path / "usage.jsonl")
    cfg = Config(
        monthly_budget_usd=0.01, tasks={"extract": "m"}, providers={"m": ProviderConfig("m", "mock")}
    )
    router = Router(cfg, ledger)
    req = LLMRequest(
        task="extract", system="s", user="Glucosa   92 mg/dL   70 - 99", schema=extraction_json_schema()
    )
    router.complete_json(req)  # gasto 0: pasa
    ledger.record("extract", LLMResponse(data={}, provider="m", model="m", cost_usd=0.02))
    with pytest.raises(BudgetExceeded):
        router.complete_json(req)
    lines = (tmp_path / "usage.jsonl").read_text().splitlines()
    assert all("Glucosa" not in ln for ln in lines)  # la bitácora nunca guarda contenido


class FakeMessages:
    def __init__(self, response):
        self.response, self.kwargs = response, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


class Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class Usage:
    input_tokens, output_tokens = 1000, 500


class Resp:
    def __init__(self, text, stop="end_turn"):
        self.content, self.stop_reason, self.usage, self._request_id = [Block(text)], stop, Usage(), "req_1"


def test_anthropic_adapter_request_shape_and_parsing():
    payload = {"document_type": "laboratorio", "collected_on": None, "lab_name": None, "rows": []}
    client = FakeClient(Resp(json.dumps(payload)))
    p = AnthropicProvider(
        "claude", "claude-opus-5-5", effort="medium", input_per_mtok=4.0, output_per_mtok=20.0, client=client
    )
    out = p.complete_json(LLMRequest(task="extract", system="s", user="u", schema=extraction_json_schema()))
    kw = client.messages.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "medium"
    # parámetros que los modelos actuales rechazan no deben enviarse
    for banned in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
        assert banned not in kw
    assert out.data == payload and out.request_id == "req_1"
    assert out.cost_usd == pytest.approx(0.004 + 0.01)


def test_anthropic_adapter_refusal_and_truncation():
    for stop, exc in (("refusal", ProviderRefusal), ("max_tokens", ProviderError)):
        p = AnthropicProvider("c", "m", client=FakeClient(Resp("{}", stop)))
        with pytest.raises(exc):
            p.complete_json(LLMRequest(task="extract", system="s", user="u", schema={}))


def test_anthropic_adapter_error_message_has_no_prompt():
    class Boom:
        class messages:  # noqa: N801
            @staticmethod
            def create(**_):
                raise RuntimeError("contenido secreto del paciente")

    p = AnthropicProvider("c", "m", client=Boom())
    with pytest.raises(ProviderError) as e:
        p.complete_json(LLMRequest(task="extract", system="s", user="dato privado", schema={}))
    assert "secreto" not in str(e.value) and "privado" not in str(e.value)


def test_schema_is_strict():
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(extraction_json_schema())


def test_ratio_printed_with_mg_dl_is_not_a_conversion():
    line = "INDICE ATEROGENICO 5.0 * mg/dL <3.5"
    raw = RawExtraction(
        document_type="laboratorio",
        collected_on=None,
        lab_name=None,
        rows=[
            RawRow(
                analyte_name="INDICE ATEROGENICO",
                value_text="5.0",
                unit_text="mg/dL",
                ref_text="<3.5",
                evidence=line,
            )
        ],
    )
    (row,) = process(raw, line)
    assert (row.key, row.value, row.unit) == ("chol_hdl_ratio", 5.0, "")
    assert not row.converted and not row.problems and row.status == "high"
