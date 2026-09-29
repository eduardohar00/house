"""Proveedor sin IA para pruebas y como línea base del banco de pruebas.

Extrae filas con una expresión regular sobre líneas tipo:
    Glucosa 92 mg/dL 70 - 99   (con uno o varios espacios entre columnas)
Sirve para (1) correr todo sin red ni costo y (2) medir cuánto aporta un modelo real
frente a una regla simple.
"""

from __future__ import annotations

import re
import time
import unicodedata

from .base import LLMRequest, LLMResponse

# Independiente del espaciado (los PDF suelen colapsar las columnas a un solo espacio).
# Acepta dos órdenes de columnas: "valor unidad referencia" y "valor referencia unidad" (Chopo).
# Sin unidad impresa exige un intervalo de referencia; así no confunde números del nombre ("25-OH")
# ni leyendas ("ALTO 200 - 499"). El "*" tras un valor fuera de rango se tolera.
_UNIT = r"x?\s?10\^?\d+/[A-Za-zµμ]+|[A-Za-zµμ]+/[A-Za-zµμ0-9.]+(?:/[A-Za-zµμ0-9.]+)?|%|[fF][lL]|pg|UCT"
_WORD_UNIT = r"días|dias|mL|cm|millones"
_REF = r"[<>]\s*=?\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[-–]\s*\d+(?:[.,]\d+)?"
_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-zÁÉÍÓÚÑáéíóúñ]\S*(?:\s+\S+)*?)\s+(?P<value>\d+(?:[.,]\d+)?)(?:\s*\*)?"
    rf"(?:\s*(?P<unit>{_UNIT})(?:\s+(?P<ref>\S.*?))?"
    rf"|\s+(?P<ref_only>{_REF})(?:\s+(?P<unit_after>{_UNIT}|{_WORD_UNIT})(?:\s*\(.*\))?)?)\s*$"
)
# Unidad sola en el renglón siguiente (Chopo parte "4.70-5.80 / millones/µL").
_UNIT_LINE = re.compile(rf"^\s*(?:{_UNIT}|{_WORD_UNIT})\s*$")
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_SHORT_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4}|\d{2})\b")

# Resultados de texto frecuentes, de la frase más larga a la más corta (sin acentos, minúsculas).
_TEXT_RESULTS = sorted(
    (
        "no reactivo",
        "urato amorfo",
        "gris opalescente",
        "blanco grisaceo",
        "ligera turbidez",
        "negativo",
        "negativa",
        "positivo",
        "positiva",
        "reactivo",
        "ausentes",
        "ausente",
        "presentes",
        "presente",
        "escasas",
        "escasos",
        "escasa",
        "abundantes",
        "moderados",
        "normal",
        "amarillo",
        "ambar",
        "transparente",
        "claro",
        "turbio",
        "turbidez",
        "incompleta",
        "completa",
        "opalescente",
        "grisaceo",
    ),
    key=lambda p: -len(p.split()),
)
# "Método: Quimioluminiscencia" aplica a los resultados anteriores que aún no tienen método.
_METHOD = re.compile(r"^\s*m[ée]todo\s*:\s*(?P<m>\S.*?)\s*$", re.I)
_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")


def _plain(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]", "", s)


def _header(line: str) -> str | None:
    """Encabezado de sección: renglón en mayúsculas sin datos ("EXAMEN GENERAL DE ORINA")."""
    s = line.strip().rstrip("_ ").strip()
    if len(re.sub(r"[^A-Za-zÁÉÍÓÚÑ]", "", s)) < 4 or re.search(r"[a-záéíóúñ]", s):
        return None
    if re.search(r"[,:]|\d-|-\d|RESULTADO|SUCURSAL|S\.A\.|INFORME", s):
        return None
    return s


def _is_subsection(header: str) -> bool:
    p = _plain(header)
    return p.startswith("examen ") and any(w in p for w in ("fisico", "quimico", "microscop"))


def _text_row(line: str) -> dict | None:
    """'Nitritos Negativo Negativo' -> nombre, resultado de texto y referencia. Si justo antes del
    resultado de texto hay una cifra ('Bilirrubina 1 Negativo ó < 0.2'), la cifra es el valor."""
    if "," in line or not re.match(r"^\s*[A-Za-zÁÉÍÓÚÑáéíóúñ]", line):
        return None
    toks = line.split()
    plain = [_plain(t) for t in toks]
    for k in range(1, len(toks)):
        for phrase in _TEXT_RESULTS:
            n = len(phrase.split())
            if " ".join(plain[k : k + n]) != phrase:
                continue
            name, rest = toks[:k], toks[k:]
            if name[-1] == ".":
                return None  # "Cristales . Ausentes": sin resultado; el tipo viene en el renglón siguiente
            if _NUMBER.match(name[-1]) and len(name) > 1:
                name, value, rest = name[:-1], name[-1], rest
            else:
                value, rest = " ".join(toks[k : k + n]), toks[k + n :]
            if len(name) > 6 or len(rest) > 6 or re.search(r"[:<>=]", " ".join(name)):
                return None
            if not rest and not re.search(r"[a-záéíóúñ]", line):
                return None  # "BIOMETRIA HEMATICA COMPLETA" es un encabezado, no un resultado
            unit = None
            if rest and rest[0] == "/" and len(rest) > 1:  # "AUSENTES / Campo"
                unit = f"/{rest[1]}"
                rest = rest[2:]
            elif rest and "/" in rest[0] and _NUMBER.match(value) is None:
                unit = rest.pop(0)
            return {
                "analyte_name": " ".join(name),
                "value_text": value,
                "unit_text": unit,
                "ref_text": " ".join(rest) or None,
            }
    return None


class BaselineRegexProvider:
    def __init__(self, name: str = "baseline-regex", **_: object) -> None:
        self.name = name

    def complete_json(self, req: LLMRequest) -> LLMResponse:
        t0 = time.monotonic()
        rows, collected, registered, dated = [], None, None, None
        major, sub = None, None
        lines = req.user.splitlines()
        for i, line in enumerate(lines):
            if collected is None and re.search(r"toma|recolecci|muestra", line, re.I):
                m = _DATE.search(line)
                if m:
                    collected = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            if registered is None and re.search(r"registro", line, re.I):
                m = _DATE.search(line)
                if m:
                    registered = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            # Último recurso: una "Fecha:" cualquiera que no sea de nacimiento (año de 2 o 4 cifras).
            if dated is None and re.search(r"\bfecha\b", line, re.I) and not re.search(r"nac", line, re.I):
                m = _SHORT_DATE.search(line)
                if m:
                    year = m.group(3) if len(m.group(3)) == 4 else f"20{m.group(3)}"
                    dated = f"{year}-{m.group(2)}-{m.group(1)}"
            if mm := _METHOD.match(line):
                for r in rows:
                    r["method"] = r["method"] or mm["m"]
                continue
            section = f"{major} > {sub}" if major and sub else major
            m = _LINE.match(line) if ", " not in line else None  # ", ": texto explicativo
            if m and re.search(r"[<>=]", m.group("name")):
                m = None  # "Leucocitos Negativo Negativo ó < 10 leu/uL": el 10 es la referencia
            if m and m.group("name").rstrip().endswith(":"):
                m = None  # "Normal: 4.0% a 5.7%": leyenda de interpretación, no un resultado
            if m:
                row = {
                    "analyte_name": m.group("name").strip(),
                    "value_text": m.group("value"),
                    "unit_text": m.group("unit") or m.group("unit_after"),
                    "ref_text": (m.group("ref") or m.group("ref_only") or "").strip() or None,
                }
            else:
                row = _text_row(line)
                # "Ligera" / "Aspecto turbidez Claro": la primera palabra del resultado quedó sola en el
                # renglón anterior; se une para leer "Ligera turbidez".
                prev = lines[i - 1].strip() if i else ""
                if (
                    row
                    and re.fullmatch(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+", prev)
                    and not _NUMBER.match(row["value_text"])
                ):
                    row["value_text"] = f"{prev} {row['value_text']}"
                    line = f"{lines[i - 1]}\n{line}"
            if row:
                if row["unit_text"] is None and i + 1 < len(lines) and _UNIT_LINE.match(lines[i + 1]):
                    row["unit_text"] = lines[i + 1].strip()
                rows.append({**row, "evidence": line.strip(), "section": section, "method": None})
            elif h := _header(line):
                if _is_subsection(h):
                    sub = h
                else:
                    major, sub = h, None
        data = {
            "document_type": "laboratorio" if rows else "otro",
            "collected_on": collected or registered or dated,
            "lab_name": None,
            "rows": rows,
        }
        return LLMResponse(
            data=data,
            provider=self.name,
            model="regex",
            cost_usd=0.0,
            latency_s=time.monotonic() - t0,
        )
