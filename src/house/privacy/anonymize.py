"""Quita datos personales del texto ANTES de enviarlo a cualquier proveedor de IA.

Es un limpiador por reglas para documentos mexicanos (CURP, RFC, folios, teléfonos,
correo, fecha de nacimiento, nombres conocidos y encabezados de paciente o médico).
Límites conocidos: no detecta nombres que no estén en la lista ni en un encabezado
reconocible. Por eso la app muestra siempre "Lo que ve la IA" antes de enviar, y la fase 1
añadirá un detector de entidades (Presidio) como segunda capa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

CURP = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b")
RFC = re.compile(r"\b[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<!\d)(?:\+?52[\s.-]?)?(?:\(?\d{2,3}\)?[\s.-]?)\d{3,4}[\s.-]?\d{4}(?!\d)")
LABELED_ID = re.compile(
    r"(?im)^(?P<label>\s*(?:folio|orden|no\.?\s*de\s*(?:orden|registro|expediente)|registro|"
    r"expediente|nss|id\s*paciente)[^:\n]{0,20}[:#]\s*)(?P<val>\S[^\n]*?)(?=\s{2,}|\s·|$)"
)
# Números administrativos en cualquier parte del renglón ("56392629 Orden:OK0350125",
# "Episodio : 1501338810 Habitación/Cama: LMCA411-Cama411") y códigos solos al inicio de renglón.
INLINE_ID = re.compile(
    r"(?i)(?P<label>\b(?:orden|solicitud|episodio|habitaci[oó]n/cama|n[°º]\s*paciente|id\s*paciente)"
    r"[ \t]*:[ \t]*)(?P<val>[A-Z0-9][A-Z0-9-]*)"
)
ID_AT_LINE_START = re.compile(r"(?m)^(?:\d{7,}|[A-Z]{1,3}\d{6,})(?=[ \t]|$)")
ADDRESS_LINE = re.compile(r"(?im)^(?P<label>\s*(?:domicilio|direcci[oó]n|calle)\s*:\s*).+$")
DOB = re.compile(
    r"(?i)(?P<label>(?:fecha\s+de\s+nacimiento|fec(?:ha)?\.?[ \t]*(?:de[ \t]+)?nac(?:\.|imiento)?)"
    r"[ \t]*:?[ \t]*)"
    r"(?P<d>\d{1,2})[/-](?P<m>\d{1,2})[/-](?P<y>\d{4})"
)
# Nombre tras un encabezado ("Paciente: ORTIZ HARO", "Médico : Dr. NOMBRE", "Dirigido a:DR(A). NOMBRE").
# Nunca cruza de renglón: [ \t] en vez de \s, para no juntar líneas y perder resultados.
# El nombre exige mayúscula inicial aunque la etiqueta se busque sin distinguir mayúsculas.
HEADER_NAME = re.compile(
    r"(?im)(?P<label>\b(?:paciente|nombre|m[eé]dico(?:[ \t]+solicitante)?|solicit[oó]|referido[ \t]+por|"
    r"dirigido[ \t]+a|dra?)\b[ \t]*[:.]?[ \t]*(?:(?:dr\(a\)|dra?)\.?[ \t]*)?)"
    r"(?P<name>(?-i:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'-]+)"
    r"(?:[ \t](?!(?:sexo|edad|hoja|fecha|folio|orden|solicitud)\b)(?-i:[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ'-]+)){0,4})"
)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Redaction:
    kind: str  # NOMBRE | CURP | RFC | CORREO | TELEFONO | FOLIO | DIRECCION | EDAD
    placeholder: str


@dataclass(frozen=True)
class ScrubResult:
    text: str
    redactions: tuple[Redaction, ...]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.redactions:
            out[r.kind] = out.get(r.kind, 0) + 1
        return out


class Anonymizer:
    def __init__(self, known_names: list[str] | None = None) -> None:
        # Cada palabra de cada nombre conocido (>= 3 letras) se busca sin acentos ni mayúsculas.
        words = {w for n in (known_names or []) for w in re.findall(r"\w+", _fold(n)) if len(w) >= 3}
        self._name_re = (
            re.compile(r"\b(" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b")
            if words
            else None
        )

    def scrub(self, text: str, *, reference_date: date | None = None) -> ScrubResult:
        found: list[Redaction] = []

        def sub(pattern: re.Pattern[str], kind: str, s: str, repl: str | None = None) -> str:
            ph = repl or f"[{kind}]"

            def _r(m: re.Match[str]) -> str:
                found.append(Redaction(kind, ph))
                return ph

            return pattern.sub(_r, s)

        s = text

        def dob(m: re.Match[str]) -> str:
            ref = reference_date or date.today()
            born = date(int(m["y"]), int(m["m"]), int(m["d"]))
            age = ref.year - born.year - ((ref.month, ref.day) < (born.month, born.day))
            found.append(Redaction("EDAD", f"[EDAD: {age} años]"))
            return f"{m['label']}[EDAD: {age} años]"

        try:
            s = DOB.sub(dob, s)
        except ValueError:
            s = DOB.sub(lambda m: (found.append(Redaction("EDAD", "[FECHA_NAC]")), "[FECHA_NAC]")[1], s)

        def labeled(m: re.Match[str]) -> str:
            found.append(Redaction("FOLIO", "[FOLIO]"))
            return f"{m['label']}[FOLIO]"

        s = LABELED_ID.sub(labeled, s)
        s = INLINE_ID.sub(labeled, s)
        s = sub(ID_AT_LINE_START, "FOLIO", s)

        def addr(m: re.Match[str]) -> str:
            found.append(Redaction("DIRECCION", "[DIRECCION]"))
            return f"{m['label']}[DIRECCION]"

        s = ADDRESS_LINE.sub(addr, s)
        s = sub(CURP, "CURP", s)
        s = sub(RFC, "RFC", s)
        s = sub(EMAIL, "CORREO", s)
        s = sub(PHONE, "TELEFONO", s)

        def hdr(m: re.Match[str]) -> str:
            found.append(Redaction("NOMBRE", "[NOMBRE]"))
            return f"{m['label']}[NOMBRE]"

        s = HEADER_NAME.sub(hdr, s)
        if self._name_re:
            out, pos = [], 0
            folded = _fold(s)  # misma longitud que s salvo combinaciones raras; se valida abajo
            if len(folded) == len(s):
                for m in self._name_re.finditer(folded):
                    out.append(s[pos : m.start()])
                    out.append("[NOMBRE]")
                    found.append(Redaction("NOMBRE", "[NOMBRE]"))
                    pos = m.end()
                out.append(s[pos:])
                s = "".join(out)
        return ScrubResult(text=s, redactions=tuple(found))
