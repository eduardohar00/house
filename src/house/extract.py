"""Extracción de un documento de laboratorio: anonimizar, pedir a la IA, VERIFICAR, normalizar.

Principio: la IA lee y estructura; el código valida. Una fila solo pasa como confiable si su
evidencia existe literalmente en el texto enviado y su valor aparece en esa evidencia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .normalize import ranges, terminology, units
from .privacy import Anonymizer, ScrubResult
from .providers import LLMRequest, LLMResponse, Router
from .schema import RawExtraction, extraction_json_schema

SYSTEM_PROMPT = """Eres un asistente que lee informes de laboratorio clínico en español o inglés.
Extrae cada resultado tal como está impreso, sea una cifra ("92") o un texto ("Negativo",
"Ausentes", "No reactivo"). No calcules, no conviertas unidades, no corrijas valores y no infieras
datos que no estén en el texto. Para cada fila copia en "evidence" la línea completa del documento
de donde salió y en "section" el encabezado de la sección donde aparece (p. ej. "EXAMEN GENERAL DE
ORINA > EXAMEN MICROSCÓPICO"), porque un mismo nombre cambia de sentido según la sección. Si un
dato no aparece, usa null. Los marcadores como [NOMBRE] o [FOLIO] son datos personales ya
eliminados: ignóralos."""


class Provenance:
    OK = "ok"
    NOT_GROUNDED = "no_respaldada_por_el_documento"
    UNKNOWN_ANALYTE = "analito_desconocido"
    UNIT_PROBLEM = "unidad_no_reconocida"
    NOT_NUMERIC = "valor_no_numerico"
    IMPLAUSIBLE = "valor_implausible"


@dataclass
class Row:
    printed_name: str
    value_text: str
    unit_text: str | None
    evidence: str
    key: str | None = None
    name: str | None = None
    loinc: str | None = None
    value: float | None = None
    value_label: str | None = None  # resultado de texto ("Negativo"); value queda en None
    unit: str | None = None
    ref_text: str | None = None
    section: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    status: str | None = None
    problems: list[str] = field(default_factory=list)
    converted: bool = False

    @property
    def needs_review(self) -> bool:
        return bool(self.problems) or self.converted


@dataclass
class Outcome:
    document_type: str
    collected_on: str | None
    rows: list[Row]
    redactions: dict[str, int]
    llm: LLMResponse
    sent_text: str


def _compact(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _to_float(s: str) -> float | None:
    try:
        return float(s.strip().replace(",", "."))
    except ValueError:
        return None


def _grounded(row_evidence: str, value_text: str, sent_text: str) -> bool:
    ev = _compact(row_evidence)
    return bool(ev) and ev in _compact(sent_text) and _compact(value_text) in ev


# Límites de plausibilidad por analito (en unidad canónica). Fuera de esto se marca para revisión.
_PLAUSIBLE = {
    "glucose": (20, 800),
    "hba1c": (3, 20),
    "chol_total": (50, 600),
    "ldl": (10, 500),
    "hdl": (5, 150),
    "triglycerides": (20, 3000),
    "crp_hs": (0, 300),
    "alt": (1, 3000),
    "creatinine": (0.1, 20),
    "hemoglobin": (3, 25),
    "ferritin": (1, 5000),
    "tsh": (0.01, 150),
    "vitamin_d": (1, 200),
}


def build_prompt(document_text: str) -> str:
    return f"Texto del documento (ya sin datos personales):\n\n{document_text}"


def process(raw: RawExtraction, sent_text: str) -> list[Row]:
    rows: list[Row] = []
    for r in raw.rows:
        row = Row(
            r.analyte_name, r.value_text, r.unit_text, r.evidence, ref_text=r.ref_text, section=r.section
        )
        if not _grounded(r.evidence, r.value_text, sent_text):
            row.problems.append(Provenance.NOT_GROUNDED)
        analyte = terminology.match_analyte(r.analyte_name, r.unit_text, r.section)
        value = _to_float(r.value_text)
        if analyte is None:
            row.problems.append(Provenance.UNKNOWN_ANALYTE)
        else:
            row.key, row.name, row.loinc = analyte.key, analyte.name, analyte.loinc
        if value is None:
            if analyte and analyte.kind in ("qual", "mixed"):
                row.value_label, row.unit = r.value_text.strip(), ""
                row.status = ranges.classify_text(r.value_text, r.ref_text)
            else:
                row.problems.append(Provenance.NOT_NUMERIC)
        elif analyte and analyte.kind == "qual":
            # Conteo en un análisis de texto (p. ej. "2" leucocitos por campo): sin conversión.
            ref = ranges.parse_ref_full(r.ref_text)
            row.value, row.unit, row.ref_low, row.ref_high = value, "", ref.low, ref.high
            row.status = ranges.classify_ref(value, ref)
        elif analyte:
            try:
                row.value, row.unit = units.to_canonical(analyte.key, value, r.unit_text, analyte.unit)
                row.converted = not units.same_unit(analyte.key, r.unit_text, analyte.unit)
            except units.UnknownUnit:
                row.problems.append(Provenance.UNIT_PROBLEM)
            if row.value is not None:
                lo, hi = _PLAUSIBLE.get(analyte.key, (0, float("inf")))
                if not lo <= row.value <= hi:
                    row.problems.append(Provenance.IMPLAUSIBLE)
                ref = ranges.parse_ref_full(r.ref_text)
                row.ref_low, row.ref_high = ref.low, ref.high
                if not row.converted:
                    row.status = ranges.classify_ref(row.value, ref)
        rows.append(row)
    return rows


def extract_document(
    text: str,
    router: Router,
    *,
    anonymizer: Anonymizer | None = None,
    reference_date: date | None = None,
    effort: str | None = None,
) -> Outcome:
    scrub: ScrubResult = (anonymizer or Anonymizer()).scrub(text, reference_date=reference_date)
    req = LLMRequest(
        task="extract",
        system=SYSTEM_PROMPT,
        user=build_prompt(scrub.text),
        schema=extraction_json_schema(),
        effort=effort,
    )
    resp = router.complete_json(req)
    raw = RawExtraction.model_validate(resp.data)
    return Outcome(
        document_type=raw.document_type,
        collected_on=raw.collected_on,
        rows=process(raw, req.user),
        redactions=scrub.counts(),
        llm=resp,
        sent_text=scrub.text,
    )
