"""Informes de imagen (radiografía, ultrasonido, resonancia, tomografía...): lectura de secciones.

Los informes de radiología mexicanos comparten una estructura: encabezado con fecha y nombre del
estudio, y secciones "Técnica", "Indicación", "Hallazgos", "Estudio previo", "Conclusión" y
"Sugerencias". Este lector determinista la separa sin IA; un mismo PDF puede traer varios informes.
Si el formato es otro, devuelve una lista vacía y la app pide leerlo con Claude.

La marca "normal" es solo una ayuda: nunca reemplaza que la persona lea la conclusión.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip

# Encabezado de sección (sin acentos, minúsculas y sin espacios: "T écnica." también coincide).
SECTIONS = {
    "tecnica": "technique",
    "indicaciondelestudio": "indication",
    "indicacion": "indication",
    "hallazgos": "findings",
    "estudioprevio": "prior",
    "estudiosprevios": "prior",
    "conclusion": "conclusion",
    "conclusiones": "conclusion",
    "sugerencias": "suggestions",
    "atentamente": "signature",
}

MODALITIES = [
    ("radiograf", "Radiografía"),
    ("rayos x", "Radiografía"),
    ("ultrason", "Ultrasonido"),
    ("ecograf", "Ultrasonido"),
    ("resonancia", "Resonancia magnética"),
    ("tomograf", "Tomografía"),
    ("mastograf", "Mastografía"),
    ("densitometr", "Densitometría"),
    ("electrocardiograma", "Electrocardiograma"),
    ("ecocardiograma", "Ecocardiograma"),
]


@dataclass
class ImagingReport:
    study_name: str
    performed_on: str | None = None
    modality: str | None = None
    technique: str = ""
    indication: str = ""
    findings: str = ""
    prior: str = ""
    conclusion: str = ""
    suggestions: str = ""
    radiologist: str = ""
    site: str = ""
    flag: str = "revisar"  # "normal" | "revisar"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _plain(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z]+", "", s)


def parse_spanish_date(text: str) -> str | None:
    """'domingo, 4 de mayo de 2025' o '04/05/2025' -> '2025-05-04'."""
    t = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    if m := re.search(r"(\d{1,2})\s+de\s+([a-z]+)\s+(?:de\s+)?(\d{4})", t):
        month = MONTHS.get(m.group(2))
        if month:
            try:
                return date(int(m.group(3)), month, int(m.group(1))).isoformat()
            except ValueError:
                return None
    if m := re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", t):
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            return None
    return None


def detect_modality(*texts: str) -> str | None:
    blob = " ".join(texts).lower()
    for key, label in MODALITIES:
        if key in blob:
            return label
    return None


_ABNORMAL = re.compile(
    r"anormal|alteraci|lesi[oó]n|fractur|n[oó]dul|masa\b|derrame|calcific|escoliosis|hernia|osteofit|"
    r"estenosis|aumento|disminuci|opacidad|sospech|se\s+sugiere|control|seguimiento|hallazgo\s+incidental",
    re.I,
)
_NORMAL = re.compile(r"\bnormal(es)?\b", re.I)


def normal_flag(conclusion: str) -> str:
    """'normal' solo si la conclusión es breve, dice normal y no menciona nada que merezca revisión."""
    c = conclusion.strip()
    if c and len(c) <= 160 and _NORMAL.search(c) and not _ABNORMAL.search(c):
        return "normal"
    return "revisar"


def looks_like_imaging_report(text: str) -> bool:
    plain = _plain(text)
    return (
        "hallazgos" in plain and "conclusion" in plain and ("nombredelestudio" in plain or "tecnica" in plain)
    )


def _blocks(lines: list[str]) -> list[list[str]]:
    """Un informe por cada 'Nombre del estudio:'; incluye el encabezado (la fecha) que lo precede."""
    names = [i for i, ln in enumerate(lines) if _plain(ln).startswith("nombredelestudio")]
    if not names:
        return []
    starts = []
    for n, idx in enumerate(names):
        floor = names[n - 1] + 1 if n else 0
        start = idx
        for j in range(idx - 1, floor - 1, -1):
            if re.match(r"\s*Fecha\s*:", lines[j], re.I):
                start = j
                break
        starts.append(start)
    bounds = starts + [len(lines)]
    return [lines[bounds[i] : bounds[i + 1]] for i in range(len(starts))]


def _parse_block(block: list[str]) -> ImagingReport | None:
    header: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in block:
        line = raw.strip()
        if not line:
            continue
        head, _, rest = line.partition(".")
        key = SECTIONS.get(_plain(head)) if head and len(head) <= 30 else None
        if key and (rest.strip() == "" or key not in ("signature",) or True):
            current = key
            sections.setdefault(key, [])
            if rest.strip():
                sections[key].append(rest.strip())
            continue
        if current is None:
            if m := re.match(r"\s*Nombre\s+del\s+estudio\s*:\s*(.+)$", line, re.I):
                header["study_name"] = m.group(1).strip()
            elif m := re.match(r"\s*Fecha\s*:\s*(.+?)(?:\s+Reservaci[oó]n\b.*)?$", line, re.I):
                header.setdefault("date", m.group(1).strip())
            elif m := re.match(r"\s*Sucursal\s*:\s*(.+)$", line, re.I):
                header["site"] = m.group(1).strip()
            continue
        if current == "signature":
            sections["signature"].append(line)
        elif re.match(r"\s*P[aá]gina\s+\d+\s+de\s+\d+", line, re.I) or line.lower().startswith(
            "la interpretaci"
        ):
            current = None  # pie de página
        else:
            sections[current].append(line)

    if "study_name" not in header:
        return None
    join = lambda k: " ".join(sections.get(k, [])).strip()  # noqa: E731
    signature = sections.get("signature", [])
    radiologist = (
        re.sub(r"^(dr\(a\)|dra|dr)\.?\s*", "", signature[0], flags=re.I).strip().title() if signature else ""
    )
    report = ImagingReport(
        study_name=header["study_name"].title().replace(" Ap ", " AP ").replace(" Y ", " y "),
        performed_on=parse_spanish_date(header.get("date", "")),
        technique=join("technique"),
        indication=join("indication"),
        findings=join("findings"),
        prior=join("prior"),
        conclusion=join("conclusion"),
        suggestions=join("suggestions"),
        radiologist=radiologist,
        site=header.get("site", ""),
    )
    report.modality = detect_modality(report.technique, report.study_name)
    report.flag = normal_flag(report.conclusion)
    return report


def parse_reports(text: str) -> list[ImagingReport]:
    """Todos los informes que trae el texto de un PDF (lista vacía si no tiene este formato)."""
    lines = text.splitlines()
    out = []
    for block in _blocks(lines):
        report = _parse_block(block)
        if report and (report.findings or report.conclusion):
            out.append(report)
    return out
