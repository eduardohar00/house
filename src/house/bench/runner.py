"""Corre cada proveedor sobre cada caso y compara contra lo esperado."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..config import Config, ProviderConfig
from ..extract import extract_document
from ..privacy import Anonymizer
from ..providers import ProviderError, Router
from .metrics import CaseScore, score_case

BASELINE = ProviderConfig(name="baseline-regex", kind="mock")


def load_cases(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "expected.json").exists())


def read_document(case_dir: Path) -> str:
    txt = case_dir / "document.txt"
    if txt.exists():
        return txt.read_text(encoding="utf-8")
    pdf = case_dir / "document.pdf"
    if pdf.exists():
        import pdfplumber  # requiere: pip install "house[pdf]"

        with pdfplumber.open(pdf) as doc:
            return "\n".join(page.extract_text() or "" for page in doc.pages)
    raise FileNotFoundError(f"{case_dir}: falta document.txt o document.pdf")


def run(
    cases_root: Path,
    cfg: Config | None,
    provider_names: list[str] | None,
    overrides: dict | None = None,
) -> list[CaseScore]:
    providers = dict(cfg.providers) if cfg else {}
    for o in overrides or {}:
        providers.setdefault(o, ProviderConfig(name=o, kind="mock"))
    providers.setdefault(BASELINE.name, BASELINE)
    names = provider_names or [BASELINE.name]
    scores: list[CaseScore] = []
    for name in names:
        if name not in providers:
            raise SystemExit(f"Proveedor no definido en la configuración: {name}")
        base = cfg or Config()
        router = Router(
            replace(base, tasks={"extract": name}, providers=providers, monthly_budget_usd=1e12),
            overrides=overrides,
        )
        for case_dir in load_cases(cases_root):
            expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
            if expected.get("verified") is False:
                print(
                    f"AVISO: se omite {case_dir.name}: expected.json aún no está verificado", file=sys.stderr
                )
                continue
            profile_path = case_dir / "profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
            anonymizer = Anonymizer(profile.get("names", []))
            ref = date.fromisoformat(expected["collected_on"]) if expected.get("collected_on") else None
            try:
                out = extract_document(
                    read_document(case_dir), router, anonymizer=anonymizer, reference_date=ref
                )
                forbidden = profile.get("forbidden", [])
                scores.append(score_case(case_dir.name, name, expected, out, forbidden))
            except (ProviderError, ValueError) as e:
                n = len(expected["results"])
                scores.append(CaseScore(case=case_dir.name, provider=name, expected=n, error=str(e)))
    return scores
