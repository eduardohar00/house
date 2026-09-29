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

_MONTH_ABBR = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
               "set": 9, "oct": 10, "nov": 11, "dic": 12}  # fmt: skip

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
    ("radiometr", "Radiografía"),
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
    if m := re.search(r"\b(\d{1,2})[\s/.-]+([a-z]{3,10})\.?[\s/.-]+(\d{4})\b", t):
        month = MONTHS.get(m.group(2)) or _MONTH_ABBR.get(m.group(2)[:3])
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


MAX_TEXT = 20000  # hallazgos y conclusión: un informe de patología largo debe caber completo
MAX_SHORT = 2000


def detect_modality(*texts: str) -> str | None:
    blob = " ".join(texts).lower()
    if re.search(r"\brx\b", blob):
        return "Radiografía"
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


def _parse_chopo_2025(text: str) -> list[ImagingReport]:
    """Formato "Nombre del estudio: / Técnica. / Hallazgos. / Conclusión." (varios informes por PDF)."""
    lines = text.splitlines()
    out = []
    for block in _blocks(lines):
        report = _parse_block(block)
        if report and (report.findings or report.conclusion):
            out.append(report)
    return out


# ---------------------------------------------------------------------------------------------------
# Otros formatos: informe del hospital ("INFORME RADIOLÓGICO"), Chopo anterior a 2025 (Radiología,
# Ultrasonido, Cardiología) y, para lo demás (endoscopias, patología, ECG, baropodometría...), un lector
# genérico que conserva el texto y saca lo que puede.

_ADMIN = re.compile(
    r"^(?:nom(?:bre)?\.?\s*paciente|paciente|id\s*paciente|nombre\s*:|edad|sexo|fecha\s*de\s*nacimiento|"
    r"fec\.?\s*nac|impreso|order\s*id|orden\b|cp\s*:|folio|ingreso|pedido|perteneciente|solicitado|dirigido|"
    r"m[eé]dico\s*:|hoja\s+\d|p[aá]gina\s+\d|reservaci[oó]n|id\s*:|c[eé]d\.?\s*prof|escaneado\s+con|camscanner|"
    r"tel[eé]fonos?\b|www\.|num\.?\s*historia|fecha\s+nacimiento|\d{10}$|\d{5}$|"
    r"(?=.*\b(?:calle|calzada|avenida|av\.)\s)|(?=.*\d+\s*°?\s*piso\b))",
    re.I,
)
_STOP = re.compile(
    r"^(?:aprobado\s+por|atentamente|\(EM\)|especialista\s+en\s+anatom|c[eé]dula\s+profesional|"
    r"la\s+interpretaci[oó]n\s+del\s+resultado|"
    r"la\s+interpretaci[oó]n\s+de\s+los\s+resultados)",
    re.I,
)
_HEADS = [
    ("findings", re.compile(r"^t\s?[eé]cnica\s+y\s+hallazgos\b", re.I)),
    ("technique", re.compile(r"^t\s?[eé]cnica\b", re.I)),
    ("indication", re.compile(r"^(?:indicaci[oó]n(?:\s+cl[ií]nica|\s+del\s+estudio)?|motivo\s+del\s+estudio|"
                              r"diagn[oó]stico\s+pre\w*|antecedentes?)\b", re.I)),
    ("findings", re.compile(r"^hallazgos\b", re.I)),
    ("prior", re.compile(r"^estudios?\s+previos?\b", re.I)),
    ("conclusion", re.compile(r"^(?:conclusi[oó]n(?:es)?(?:\s+radiol[oó]gica)?|impresi[oó]n\s+diagn[oó]stica|"
                              r"diagn[oó]stico\s+p[oó]s\w*|diagn[oó]stico)\b", re.I)),
    ("suggestions", re.compile(r"^sugerencias?\b", re.I)),
]  # fmt: skip
_LAB_MARKERS = ("intervalo de referencia", "prueba bajo (lr)", "analisis clinicos", "resultado unidades")
_KINDS = [
    ("biopsi", "Patología"), ("patolog", "Patología"), ("histopatol", "Patología"),
    ("endoscop", "Endoscopia"), ("colonoscop", "Endoscopia"), ("gastroscop", "Endoscopia"),
    ("intestinoscop", "Endoscopia"), ("electrocardiograma", "Electrocardiograma"),
    ("baropodometr", "Baropodometría"), ("podoscan", "Baropodometría"),
    ("nutri", "Nutrición"), ("plan alimenticio", "Nutrición"),
]  # fmt: skip
_TITLE_DATE = re.compile(r"^\s*(?:\d{1,2}-\d{1,2}-\d{4}|\d{4}-\d{1,2}-\d{1,2})\s*[-–]?\s*")


def looks_like_lab(text: str) -> bool:
    """Un informe de laboratorio (columnas de resultado y referencia) se lee con el lector de laboratorio."""
    t = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t)
    if any(m in t for m in _LAB_MARKERS):
        return True
    # Sin encabezados típicos: basta un renglón "análisis conocido + valor + unidad" (laboratorio).
    from .normalize import terminology
    from .providers.mock import _LINE

    for line in text.splitlines():
        m = _LINE.match(line)
        if m and terminology.match_analyte(m.group("name").strip(), m.group("unit") or m.group("unit_after")):
            return True
    return False


def _clean_lines(text: str) -> list[str]:
    out: list[str] = []
    skip = False
    for raw in text.splitlines():
        line = re.sub(r"[\uf000-\uf0ff]", "-", raw).strip()  # viñetas de Word
        if (
            skip
        ):  # con OCR, "Paciente:" y su valor quedan en renglones distintos: el valor es un dato personal
            skip = False
            if len(line) < 60 and not _ADMIN.match(line):
                continue
        if not line or line == "\x0c":
            continue
        if _ADMIN.match(line):
            skip = line.endswith(":") and not re.match(r"^(orden|folio|hoja|p[aá]gina|id\s*:)", line, re.I)
            continue
        out.append(line)
    return out


def _sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """(antes del primer encabezado, secciones por nombre, firma) a partir de renglones ya limpios."""
    pre: list[str] = []
    secs: dict[str, list[str]] = {}
    signature: list[str] = []
    current: str | None = None
    for i, line in enumerate(lines):
        if _STOP.match(line):
            signature = lines[i : i + 3]
            break
        for key, rx in _HEADS:
            if m := rx.match(line):
                current = key
                secs.setdefault(key, [])
                rest = line[m.end() :].lstrip(" :.-")
                if rest:
                    secs[key].append(rest)
                break
        else:
            (secs[current] if current else pre).append(line)
        # La técnica y la indicación son breves: al cerrar con "." o ":" lo que sigue ya es el cuerpo.
        if current in ("technique", "indication") and line.rstrip().endswith((".", ":")):
            current = None
    return pre, secs, signature


def _doctor(signature: list[str]) -> str:
    for line in signature:
        m = re.search(r"(?:aprobado\s+por|atentamente)\s*[:,]?\s*(.*)$", line, re.I)
        name = (m.group(1) if m else "").strip()
        if not name and re.match(r"^dra?\.", line, re.I):
            name = line
        if name:
            name = re.sub(r"\s+(?:fecha|cp|c\.esp)\b.*$", "", name, flags=re.I)
            name = re.sub(r"^(dr\(a\)|dra|dr)\.?\s*", "", name, flags=re.I).strip(" .")
            if len(name.split()) >= 2 and not re.search(r"\d|\bdel\.|\bcol\.", name, re.I):
                return name.title()
    return ""


def _nice_title(s: str) -> str:
    """'RX DE RODILLA AP Y LATERAL' -> 'Rx de rodilla AP y lateral'."""
    out = []
    for w in s.lower().split():
        out.append(w.upper() if w in {"ap", "pa", "tc", "us", "pet", "ecg", "rx"} else w)
    t = " ".join(out)
    return t[:1].upper() + t[1:]


def _tidy(text: str) -> str:
    """Quita direcciones web y correos que se cuelan de los pies de página."""
    t = re.sub(r"\S+@\S+|\b(?:https?://|www\.)\S+|\b\S+\.(?:com|mx)\b", "", text)
    return re.sub(r"\s{2,}", " ", t).strip()


def _finish(r: ImagingReport) -> ImagingReport:
    r.modality = r.modality or detect_modality(r.study_name, r.technique, r.findings[:400])
    r.conclusion = _tidy(r.conclusion)
    r.flag = normal_flag(r.conclusion)
    return r


def _parse_hospital(text: str) -> list[ImagingReport]:
    """ "INFORME RADIOLÓGICO" del hospital: código de estudio con fecha, cuerpo y "Aprobado por"."""
    lines = text.splitlines()
    idx = next((i for i, ln in enumerate(lines) if re.search(r"\b[A-Z]{2,4}-\d{4,}\b", ln)), None)
    if idx is None or "informeradiol" not in _plain(text):
        return []
    code = lines[idx]
    date_m = re.search(r"\d{1,2}/\d{1,2}/\d{4}", code) or re.search(r"\d{1,2}/\d{1,2}/\d{4}", lines[idx + 1])
    name = re.sub(r"\b[A-Z]{2,4}-\d{4,}\b|\d{1,2}/\d{1,2}/\d{4}.*$", "", code).strip()
    if not name:  # el nombre ocupa el renglón anterior (y, a veces, el siguiente: "CON CONTRASTE ...")
        name = lines[idx - 1].strip()
        if idx + 1 < len(lines) and re.match(r"^con\b", lines[idx + 1].strip(), re.I):
            name += " " + lines[idx + 1].strip()
    body = [
        ln for ln in lines[idx + 1 :] if not re.match(r"^\s*(?:order\s*id|\d{1,2}/\d{1,2}/\d{4})", ln, re.I)
    ]
    pre, secs, signature = _sections(_clean_lines("\n".join(body)))
    report = ImagingReport(
        study_name=_nice_title(name),
        performed_on=parse_spanish_date(date_m.group(0)) if date_m else None,
        technique=" ".join(secs.get("technique", [])),
        indication=" ".join(secs.get("indication", [])),
        findings=" ".join(secs.get("findings") or pre),
        prior=" ".join(secs.get("prior", [])),
        conclusion=" ".join(secs.get("conclusion", [])),
        suggestions=" ".join(secs.get("suggestions", [])),
        radiologist=_doctor(signature),
        site="Imagenología",
    )
    if not report.technique and report.findings:  # "Radiografía de abdomen de pie y decúbito" abre el cuerpo
        first = (secs.get("findings") or pre)[0]
        if len(first) < 90 and detect_modality(first):
            report.technique = first
            report.findings = " ".join((secs.get("findings") or pre)[1:])
    return [_finish(report)]


def _parse_legacy_chopo(text: str) -> list[ImagingReport]:
    """Chopo antes de 2025: encabezado del servicio (RADIOLOGÍA, ULTRASONIDO...), "Fecha:" y secciones."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    svc = next((ln for ln in lines[:12] if re.fullmatch(
        r"(RADIOLOG[ÍI]A|ULTRASONIDO|CARDIOLOG[ÍI]A|MASTOGRAF[ÍI]A|DENSITOMETR[ÍI]A|TOMOGRAF[ÍI]A|RESONANCIA)",
        ln, re.I)), None)  # fmt: skip
    date_line = next((ln for ln in lines[:12] if re.match(r"^fecha\s*:", ln, re.I)), None)
    if not svc or not date_line:
        return []
    start = next((i for i, ln in enumerate(lines) if re.match(r"^dirigido", ln, re.I)), 0)
    name = next(
        (ln for ln in lines[start + 1 :] if sum(c.isupper() for c in ln) >= 8 and ln == ln.upper()
         and not re.match(r"^(apreciado|a continuaci[oó]n)", ln, re.I)),
        svc,
    )  # fmt: skip
    pre, secs, signature = _sections(_clean_lines("\n".join(lines[start + 1 :])))
    pre = [ln for ln in pre if ln != name]
    site = next((ln for ln in lines if re.match(r"^sucursal\b", ln, re.I)), "")
    report = ImagingReport(
        study_name=_nice_title(re.sub(r"^CR\s+", "", name)),
        performed_on=parse_spanish_date(date_line),
        technique=" ".join(secs.get("technique", [])),
        indication=" ".join(secs.get("indication", [])),
        findings=" ".join(secs.get("findings") or pre),
        prior=" ".join(secs.get("prior", [])),
        conclusion=" ".join(secs.get("conclusion", [])),
        suggestions=" ".join(secs.get("suggestions", [])),
        radiologist=_doctor(signature),
        site=site.title(),
    )
    return [_finish(report)]


def _title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", filename or "").strip()
    stem = _TITLE_DATE.sub("", stem)
    return re.sub(r"\s*\(\d+\)$", "", stem).strip(" -–")


def _parse_generic(text: str, filename: str = "") -> list[ImagingReport]:
    """Cualquier otro informe (endoscopia, patología, ECG...): conserva el texto y saca título, fecha y
    conclusión con reglas simples; la persona lo confirma junto al original."""
    lines = _clean_lines(text)
    if sum(len(ln) for ln in lines) < 60:
        return []
    pre, secs, signature = _sections(lines)
    title = _title_from_filename(filename) or next(
        (ln for ln in lines if ln == ln.upper() and len(ln) > 6), "Estudio"
    )
    when = None
    if m := re.search(r"(?:^|[^\d])(\d{1,2})-(\d{1,2})-(\d{4})|(\d{4})-(\d{1,2})-(\d{1,2})", filename or ""):
        when = parse_spanish_date(m.group(0).strip(" -"))
        if m.group(4):
            when = f"{m.group(4)}-{int(m.group(5)):02d}-{int(m.group(6)):02d}"
    if not when:
        when = next(
            (
                d
                for d in (
                    parse_spanish_date(ln)
                    for ln in text.splitlines()
                    if re.search(r"fecha|realizad", ln, re.I)
                )
                if d
            ),
            None,
        )
    if not when:
        when = next((d for d in (parse_spanish_date(ln) for ln in text.splitlines()) if d), None)
    kind = detect_modality(title) or next(
        (label for key, label in _KINDS if key in _plain_words(title)),
        next((label for key, label in _KINDS if key in _plain_words(text[:500])), None),
    )
    findings = " ".join(secs.get("findings") or pre)[:MAX_TEXT]
    report = ImagingReport(
        study_name=title[:1].upper() + title[1:],
        performed_on=when,
        modality=kind or "Estudio",
        technique=" ".join(secs.get("technique", []))[:MAX_SHORT],
        indication=" ".join(secs.get("indication", []))[:MAX_SHORT],
        findings=findings,
        prior=" ".join(secs.get("prior", []))[:MAX_SHORT],
        conclusion=_tidy(" ".join(secs.get("conclusion", [])))[:MAX_TEXT],
        suggestions=" ".join(secs.get("suggestions", []))[:MAX_SHORT],
        radiologist=_doctor(signature),
    )
    if report.modality == "Electrocardiograma" and not report.conclusion:
        report.conclusion = _ecg_interpretation(lines)
    report.flag = "revisar"  # sin estructura conocida no se marca "normal": la persona lo lee
    return [report]


_BULLET = re.compile(r"^(?:[•·]\s*\S|[-–]\s+\S|[-–][A-Za-zÁÉÍÓÚÑáéíóúñ])")  # «--LUES--» no es viñeta


def _ecg_interpretation(lines: list[str]) -> str:
    """Interpretación de un electrocardiograma escaneado: renglones con viñeta y el renglón que sigue a una
    viñeta si es texto corrido (no una medición como «Veloc: 25 mm/s»)."""
    out: list[str] = []
    for i, ln in enumerate(lines):
        if _BULLET.match(ln) and not re.search(r"\d", ln):  # «· 110C: CL» es ruido del escaneo
            out.append(ln.strip("•·-– ").strip())
        elif i and _BULLET.match(lines[i - 1]) and len(ln) > 25 and not re.search(r"\d|:", ln):
            out.append(ln)
    return _tidy(". ".join(x.rstrip(".") for x in out if x) + ".") if out else ""


def _plain_words(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def parse_reports(text: str, filename: str = "") -> list[ImagingReport]:
    """Todos los informes que trae el texto de un PDF: primero los formatos conocidos, luego el genérico."""
    for parser in (_parse_chopo_2025, _parse_hospital, _parse_legacy_chopo):
        found = parser(text)
        if found:
            return found
    return _parse_generic(text, filename)
