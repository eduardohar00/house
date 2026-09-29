"""Asistente «Pregunta a tu expediente».

Claude no ve la base de datos: pide datos con herramientas acotadas a UNA persona y responde solo con
lo que esas herramientas devuelven. Cada dato lleva un id de fuente (S1, S2…) que la respuesta debe citar;
aquí se valida que las citas existan y se convierten en enlaces al documento de origen.
No diagnostica ni recomienda tratamientos (ver docs/PRODUCT.md, «Fuera de alcance»).
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Any

from ..normalize import reference_ranges, terminology
from ..normalize import summary as summary_mod
from ..privacy import Anonymizer
from ..providers import Router
from . import bodyscan, health, reviews

MAX_TURNS = 20
MAX_CHARS = 2000

REFERENCE_SOURCE = {
    "printed": "laboratorio",
    "borrowed": "estudio anterior del laboratorio",
    "general": "referencia general (no es del laboratorio)",
}
STATUS_ES = {"ok": "en rango", "low": "por debajo", "high": "por encima", "abnormal": "fuera de lo esperado"}


class AssistantError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


SYSTEM = """Eres el asistente de salud de un expediente médico personal (House). Ayudas a la persona a entender su
historial completo y a cuidarse mejor. Respondes en español, en palabras simples pero con sustancia.

QUÉ PUEDES Y DEBES HACER (la persona asume la responsabilidad de confirmar todo con su médico)
- Opinar sobre sus hallazgos: qué podrían significar, qué los explicaría, qué tan relevantes son.
- Sugerir estudios de seguimiento, chequeos, tamizajes y cuidados que valdría la pena hacerse, según su edad,
  sexo, antecedentes, hábitos y resultados, y explicar por qué.
- Encontrar relaciones entre estudios, padecimientos, medicamentos, suplementos, hábitos y antecedentes que la
  persona quizá no ha notado, y decir qué conviene revisar.
- Comentar medicamentos y suplementos (sustancias activas, posibles interacciones, efectos sobre los análisis).
- Ser completo y concreto. No te limites por cautela excesiva: da tu mejor análisis y sé honesto con la
  incertidumbre. Ordena lo importante primero y marca con claridad lo que requiera atención pronta.

REGLAS DE RIGOR
1. Los datos de la persona salen SOLO de tus herramientas (empieza por get_full_history si la pregunta es
   amplia). Cada dato concreto del expediente (valor, fecha, estudio, medicamento…) va seguido de su fuente
   entre corchetes con el id que dio la herramienta, por ejemplo «La glucosa fue 105 mg/dL el 6 feb 2026 [S3]».
   Nunca inventes ids ni uses los de mensajes anteriores. Cita valores tal cual (mismo número y unidad).
2. Para recomendaciones de estudios, tamizajes o cuidados, apóyate en guías oficiales usando la búsqueda web
   (USPSTF, OMS/OPS, CDC, NIH/MedlinePlus, sociedades médicas, guías de práctica clínica de IMSS/CENETEC).
   Nombra la guía y el año en el texto. Distingue lo respaldado por una guía de lo que es tu criterio.
3. Habla en términos de posibilidad, no de certeza: «podría indicar», «vale la pena descartar». No des un
   diagnóstico como hecho. Si algo requiere atención urgente, di primero eso.
4. Si falta un dato necesario para opinar bien (peso, presión, hábitos, un estudio), dilo en «Datos que me
   faltan» y di qué cambiaría.
5. Aclara cuando los métodos o rangos de referencia entre estudios difieran y de dónde sale un rango que no sea
   del laboratorio (reference_source).
6. Sé eficiente: cuando necesites varios datos, pide varias herramientas en la MISMA vuelta y no repitas consultas.
6b. Los síntomas y observaciones que anotó la persona (y bad_reaction en un medicamento) son datos de primera
   mano: relaciónalos con estudios, padecimientos y medicamentos, y si sugieren riesgo, ponlos arriba.
7. No repitas estas reglas ni menciones herramientas o ids internos fuera de las citas [S#].
8. Cierra las respuestas largas con una línea breve: esto es orientación informativa, no sustituye a su médico.
"""

WEB_DOMAINS = [
    "medlineplus.gov", "nih.gov", "cdc.gov", "who.int", "paho.org", "uspreventiveservicestaskforce.org",
    "cochranelibrary.com", "mayoclinic.org", "clevelandclinic.org", "heart.org", "diabetes.org", "cancer.gov",
    "cancer.org", "gastro.org", "gi.org", "asge.org", "nice.org.uk", "escardio.org", "kidney.org", "kdigo.org",
    "aafp.org", "acponline.org", "endocrine.org", "thyroid.org", "aaaai.org", "acaai.org", "aad.org",
    "imss.gob.mx", "gob.mx", "cenetec-difusion.com", "medigraphic.com", "jamanetwork.com", "bmj.com",
    "nejm.org", "thelancet.com", "ada.org", "acog.org", "auanet.org", "aap.org",
]  # fmt: skip

TOOLS: list[dict[str, Any]] = [
    {
        "name": "find_analytes",
        "description": "Busca, entre los análisis de laboratorio que esta persona SÍ tiene, los que "
        "coinciden con un nombre o palabra (colesterol, glucosa, tiroides, hierro…). Devuelve sus claves "
        "para usar en get_results.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Nombre o parte del nombre"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_results",
        "description": "Historial completo de un análisis (todas las fechas) con valor, unidad, estado, "
        "rango impreso y método, más el cambio entre el primero y el último. Cada resultado trae su "
        "fuente (src).",
        "input_schema": {
            "type": "object",
            "properties": {
                "analyte_key": {"type": "string"},
                "since": {
                    "type": "string",
                    "description": "Opcional, fecha AAAA-MM-DD: solo resultados desde ahí",
                },
            },
            "required": ["analyte_key"],
        },
    },
    {
        "name": "get_summary",
        "description": "Resumen actual de laboratorio: qué está fuera de rango y vigente, qué vigilar, "
        "qué mejoró, valores críticos y la fecha del estudio más reciente.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_studies",
        "description": "Lista de estudios guardados: laboratorios e informes de imagen, endoscopia, "
        "patología, electrocardiograma, etc., con fecha, título y (en informes) la conclusión.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_imaging_report",
        "description": "Texto completo de un informe (técnica, hallazgos, conclusión) por su study_id "
        "de list_studies.",
        "input_schema": {
            "type": "object",
            "properties": {"study_id": {"type": "integer"}},
            "required": ["study_id"],
        },
    },
    {
        "name": "get_full_history",
        "description": "PANORAMA COMPLETO de la persona en una sola llamada: perfil y hábitos, medidas, padecimientos "
        "con sus estudios ligados, medicamentos y suplementos, alergias, antecedentes, cirugías, vacunas, "
        "consultas, TODAS las series de laboratorio (fecha, valor, estado) y los informes con su conclusión. "
        "Úsala primero cuando la pregunta sea amplia (revisión, relaciones entre estudios, qué estudios hacerse).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_health_profile",
        "description": "Perfil de salud (talla, tipo de sangre, tabaquismo, alcohol, ejercicio, sueño, dieta, "
        "ocupación) y todas las medidas con fecha (peso, presión arterial, cintura, frecuencia cardiaca, IMC).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_clinical_record",
        "description": "Expediente clínico: alergias, problemas de salud (con los estudios que la persona "
        "ligó a cada uno), medicamentos y suplementos alimenticios (actuales y pasados), antecedentes "
        "familiares, cirugías, vacunas y consultas, más los síntomas y observaciones que la persona anotó "
        "(qué notó, cuándo y tras qué medicamento) y los medicamentos que le caen mal (bad_reaction).",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _plain(s: str) -> str:
    return terminology._norm(s or "")


def _num(v: float) -> float | int:
    return int(v) if float(v).is_integer() else round(float(v), 4)


class Toolbox:
    """Herramientas de lectura sobre una sola persona. Registra las fuentes que entrega."""

    def __init__(self, db: sqlite3.Connection, person: sqlite3.Row, names: list[str], today: date) -> None:
        self.db, self.person_id, self.today = db, person["id"], today
        self.profile = {"sex": person["sex_at_birth"], "birth_date": person["birth_date"]}
        self._anon = Anonymizer(names)
        self.sources: dict[str, dict] = {}
        self._index: dict[tuple, str] = {}

    # -- fuentes --
    def _src(self, kind: str, document_id: int | None, title: str, on: str | None) -> str:
        key = (kind, document_id, title, on)
        if key not in self._index:
            sid = f"S{len(self._index) + 1}"
            self._index[key] = sid
            self.sources[sid] = {
                "id": sid,
                "kind": kind,
                "document_id": document_id,
                "title": title,
                "date": on,
            }
        return self._index[key]

    def _text(self, t: str | None) -> str | None:
        return self._anon.scrub(t).text if t else t

    def _observations(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT o.analyte_key, o.printed_name, o.value_num, o.qualifier, o.value_text, o.unit, "
            "o.ref_low, o.ref_high, o.ref_printed, o.status, o.method, o.collected_on, o.document_id, "
            "o.entered_manually, "
            "d.title AS document_title FROM observation o JOIN document d ON d.id = o.document_id "
            "WHERE o.person_id = ? ORDER BY o.collected_on, o.analyte_key",
            (self.person_id,),
        )
        return summary_mod.apply_references([dict(r) for r in rows], self.profile)

    def _name(self, key: str) -> str:
        a = terminology.BY_KEY.get(key)
        return a.name if a else key

    @staticmethod
    def _reference(o: dict) -> str | None:
        """Rango en la unidad del valor (el impreso puede venir en otra y ya está convertido)."""
        lo, hi = o["ref_low"], o["ref_high"]
        if lo is not None and hi is not None:
            return f"{_num(lo)} a {_num(hi)}"
        if hi is not None:
            return f"hasta {_num(hi)}"
        if lo is not None:
            return f"desde {_num(lo)}"
        return o["ref_printed"]  # resultados de texto: lo esperado según el informe

    def _value(self, o: dict) -> Any:
        if o["value_num"] is not None:
            return f"{o['qualifier']}{_num(o['value_num'])}" if o["qualifier"] else _num(o["value_num"])
        return o["value_text"]

    # -- herramientas --
    def find_analytes(self, query: str) -> dict:
        q = _plain(query)
        if not q:
            raise ValueError("Escribe qué análisis buscas.")
        by_key: dict[str, list[dict]] = {}
        for o in self._observations():
            by_key.setdefault(o["analyte_key"], []).append(o)
        found = []
        for key, rows in by_key.items():
            a = terminology.BY_KEY.get(key)
            hay = " ".join([_plain(a.name if a else key), *(_plain(x) for x in (a.aliases if a else ()))])
            if q in hay or all(w in hay for w in q.split()):
                last = rows[-1]
                found.append(
                    {
                        "analyte_key": key,
                        "name": self._name(key),
                        "unit": last["unit"],
                        "results": len(rows),
                        "last_date": last["collected_on"],
                    }
                )
        return {
            "matches": found[:12],
            "note": None if found else "No hay resultados guardados que coincidan.",
        }

    def get_results(self, analyte_key: str, since: str | None = None) -> dict:
        rows = [o for o in self._observations() if o["analyte_key"] == analyte_key]
        if since:
            date.fromisoformat(since)
            rows = [o for o in rows if o["collected_on"] >= since]
        if not rows:
            raise ValueError(
                "No hay resultados guardados de ese análisis (usa find_analytes para ver las claves)."
            )
        out = []
        for o in rows:
            out.append(
                {
                    "src": self._src("laboratorio", o["document_id"], o["document_title"], o["collected_on"]),
                    "date": o["collected_on"],
                    "value": self._value(o),
                    "unit": o["unit"] or None,
                    "status": STATUS_ES.get(o["status"], "sin referencia"),
                    "reference": self._reference(o),
                    "reference_source": REFERENCE_SOURCE.get(o.get("ref_source"), "no hay"),
                    "method": o["method"],
                    "entered_by_hand": bool(o["entered_manually"]),
                }
            )
        res: dict[str, Any] = {"analyte": self._name(analyte_key), "analyte_key": analyte_key, "results": out}
        nums = [o for o in rows if o["value_num"] is not None and not o["qualifier"]]
        if len(nums) >= 2 and nums[0]["unit"] == nums[-1]["unit"]:
            a, b = nums[0], nums[-1]
            change = {
                "from_date": a["collected_on"],
                "from": _num(a["value_num"]),
                "to_date": b["collected_on"],
                "to": _num(b["value_num"]),
                "difference": _num(b["value_num"] - a["value_num"]),
            }
            if a["value_num"]:
                change["percent"] = round(100 * (b["value_num"] - a["value_num"]) / abs(a["value_num"]), 1)
            res["change"] = change
        return res

    def get_summary(self) -> dict:
        obs = self._observations()
        if not obs:
            return {"note": "Todavía no hay resultados de laboratorio guardados."}
        sm = summary_mod.summarize(obs, self.today, self.profile)

        def item(e: dict) -> dict:
            last = e["last"]
            doc = next(
                (
                    o
                    for o in obs
                    if o["analyte_key"] == e["key"] and o["collected_on"] == last["collected_on"]
                ),
                None,
            )
            return {
                "src": self._src(
                    "laboratorio", doc["document_id"], doc["document_title"], last["collected_on"]
                )
                if doc
                else None,
                "analyte": self._name(e["key"]),
                "analyte_key": e["key"],
                "value": self._value(last),
                "unit": last["unit"] or None,
                "date": last["collected_on"],
                "status": STATUS_ES.get(last["status"], "sin referencia"),
                **({"kind": e["kind"], "trend": e["trend"]} if "kind" in e else {}),
            }

        return {
            "latest_study_date": sm["reference_date"],
            "study_is_old": sm["study_is_old"],
            "critical_values": [
                {**item({"key": c["key"], "last": c["last"]}), "side": c["side"]} for c in sm["critical"]
            ],
            "out_of_range_now": [item(e) for e in sm["attention"] if not e["derived"]][:25],
            "calculated_out_of_range": [item(e) for e in sm["attention"] if e["derived"]][:10],
            "to_watch": [item(e) for e in sm["watch"]][:15],
            "improved": [item(e) for e in sm["improved"]][:15],
            "counts": sm["counts"],
        }

    def list_studies(self) -> dict:
        docs = self.db.execute(
            "SELECT d.id, d.doc_type, d.title, d.collected_on, "
            "(SELECT COUNT(*) FROM observation o WHERE o.document_id = d.id) AS results "
            "FROM document d WHERE d.person_id = ? AND d.review_state = 'revisada' "
            "ORDER BY COALESCE(d.collected_on, d.uploaded_at) DESC",
            (self.person_id,),
        ).fetchall()
        studies = []
        for d in docs:
            if d["doc_type"] == "imagen":
                for i in self.db.execute(
                    "SELECT id, study_name, modality, performed_on, conclusion, flag, tables_json "
                    "FROM imaging_study "
                    "WHERE document_id = ? ORDER BY id",
                    (d["id"],),
                ):
                    studies.append(
                        {
                            "src": self._src(
                                "informe", d["id"], i["study_name"] or d["title"], i["performed_on"]
                            ),
                            "type": i["modality"],
                            "study_id": i["id"],
                            "title": i["study_name"],
                            "date": i["performed_on"],
                            "conclusion": self._text(i["conclusion"]),
                            **self._tables(i["tables_json"]),
                        }
                    )
            else:
                kind = {"laboratorio": "laboratorio", "receta": "receta"}.get(d["doc_type"], "documento")
                studies.append(
                    {
                        "src": self._src(kind, d["id"], d["title"], d["collected_on"]),
                        "type": kind if kind != "documento" else d["doc_type"],
                        "title": d["title"],
                        "date": d["collected_on"],
                        **({"results": d["results"]} if kind == "laboratorio" else {}),
                    }
                )
        return {"studies": studies}

    @staticmethod
    def _tables(raw: str | None) -> dict:
        """Tablas transcritas de un informe escaneado (p. ej. pruebas cutáneas), en forma compacta."""
        if not raw:
            return {}
        data = json.loads(raw)
        tables = [
            {"caption": t.get("caption"), "columns": t["columns"], "rows": t["rows"]}
            for t in data.get("tables", [])
        ]
        return {"tables": tables, "table_notes": data.get("notes")}

    def get_imaging_report(self, study_id: int) -> dict:
        r = self.db.execute(
            "SELECT i.*, d.title AS doc_title FROM imaging_study i "
            "LEFT JOIN document d ON d.id = i.document_id "
            "WHERE i.id = ? AND i.person_id = ?",
            (study_id, self.person_id),
        ).fetchone()
        if r is None:
            raise ValueError("No existe ese informe para esta persona.")
        return {
            "src": self._src(
                "informe", r["document_id"], r["study_name"] or r["doc_title"] or "Informe", r["performed_on"]
            ),
            "study": r["study_name"],
            "type": r["modality"],
            "date": r["performed_on"],
            **{
                k: self._text(r[k])
                for k in ("technique", "indication", "findings", "prior", "conclusion", "suggestions")
            },
            "marked_by_house": r["flag"],
            **self._tables(r["tables_json"]),
        }

    def _linked(self, problem: dict) -> list[dict]:
        """Estudios que la persona ligó a este padecimiento, cada uno con su fuente."""
        out = []
        for link in problem.get("links", []):
            item: dict[str, Any] = {"title": link["title"], "date": link["date"]}
            if link["kind"] in ("medication", "procedure"):
                item.update({"type": "tratamiento" if link["kind"] == "medication" else "cirugía",
                             "dose": link.get("value"), "detail": link.get("extra"),
                             "active": link.get("active")})  # fmt: skip
                item["src"] = self._src("expediente", None, "Expediente clínico", None)
            elif link["kind"] == "analyte":
                item.update({"analyte_key": link["ref"], "last_value": link.get("value"),
                             "status": STATUS_ES.get(link.get("status"), "sin referencia")})  # fmt: skip
            else:
                kind = "informe" if link["kind"] == "imaging" else "laboratorio"
                item["src"] = self._src(kind, link.get("document_id"), link["title"], link["date"])
            out.append(item)
        return out

    def get_clinical_record(self) -> dict:
        from . import clinical

        c = clinical.overview(self.db, self.person_id)
        src = self._src("expediente", None, "Expediente clínico", None)

        def pick(rows: list[dict], *keys: str) -> list[dict]:
            return [{k: self._text(str(r[k])) if r[k] is not None else None for k in keys} for r in rows]

        return {
            "src": src,
            "allergies": pick(c["allergies"], "substance", "category", "reaction"),
            "problems": [
                {**pick([p], "name", "status", "since_year")[0], "linked_studies": self._linked(p)}
                for p in c["problems"]
            ],
            "medications": pick(
                c["medications"],
                *(
                    "name",
                    "active_ingredient",
                    "brand",
                    "dose",
                    "reason",
                    "since_year",
                    "until_year",
                    "active",
                    "bad_reaction",
                ),
            ),
            "symptoms_and_observations": pick(c["symptoms"], "occurred_on", "what", "related", "notes"),
            "supplements": pick(
                c["supplements"], "name", "dose", "brand", "reason", "since_year", "until_year", "active"
            ),
            "family_history": pick(c["family"], "relative", "condition"),
            "procedures": pick(c["procedures"], "name", "year"),
            "vaccines": pick(c["vaccines"], "name", "brand", "dose_label", "given_on"),
            "consultations": pick(c["consultations"], "occurred_on", "reason", "specialty", "notes"),
            "confirmed_none": [
                "sin alergias a medicamentos conocidas" if x == "allergy" else x for x in c["none"]
            ],
        }

    def get_health_profile(self) -> dict:
        h = health.overview(self.db, self.person_id)
        age = reference_ranges.age_on(self.profile["birth_date"], self.today)
        return {
            "sex": "mujer" if self.profile["sex"] == "F" else "hombre",
            "age_years": age,
            "profile": {k: v for k, v in h["profile"].items() if v not in (None, "") and k != "updated_at"},
            "measurements": [
                {"kind": m["kind"], "value": m["value"], "value2": m["value2"], "date": m["measured_on"], "notes": m["notes"]}
                for m in h["measurements"]
            ],
            "bmi": h["bmi"],
            "body_composition_reports": [
                {
                    "date": sc["measured_on"],
                    "device": sc.get("device"),
                    "metrics": [
                        {k: m[k] for k in ("name", "value", "unit", "normal_low", "normal_high", "evaluation")}
                        for m in sc["metrics"]
                    ],
                    "segments": sc["segments"],
                    "notes": sc.get("notes"),
                }
                for sc in bodyscan.scans(self.db, self.person_id, 4)
            ],
        }  # fmt: skip

    def person_context(self) -> str:
        """Una línea con lo esencial de la persona para el mensaje inicial (sin nombre ni fecha de nacimiento)."""
        h = self.get_health_profile()
        p, latest = h["profile"], {}
        for m in h["measurements"]:
            latest.setdefault(m["kind"], m)
        bits = [f"{h['sex']} de {h['age_years']} años" if h["age_years"] is not None else h["sex"]]
        if p.get("height_cm"):
            bits.append(f"talla {p['height_cm']:g} cm")
        if "weight_kg" in latest:
            w = latest["weight_kg"]
            bits.append(f"peso {w['value']:g} kg ({w['date']})" + (f", IMC {h['bmi']}" if h["bmi"] else ""))
        if "blood_pressure" in latest:
            b = latest["blood_pressure"]
            bits.append(f"presión {b['value']:g}/{b['value2']:g} ({b['date']})")
        if h["body_composition_reports"]:
            sc = h["body_composition_reports"][0]
            vals = {m["name"]: m for m in sc["metrics"]}
            parts = [
                f"{n.lower()} {vals[n]['value']:g} {vals[n]['unit']}".strip()
                for n in (
                    "Masa muscular esquelética",
                    "Porcentaje de grasa corporal",
                    "Relación cintura-cadera",
                )
                if n in vals
            ]
            if parts:
                bits.append(f"composición corporal ({sc['date']}): " + ", ".join(parts))
        for label, key in (("tabaquismo", "smoking"), ("alcohol", "alcohol"), ("ejercicio", "exercise")):
            if p.get(key):
                extra = p.get(f"{key}_detail")
                bits.append(f"{label}: {p[key].lower()}" + (f" ({extra})" if extra else ""))
        if p.get("sleep_hours"):
            bits.append(f"sueño {p['sleep_hours']:g} h")
        for label, key in (("dieta", "diet"), ("ocupación", "occupation"), ("notas", "notes")):
            if p.get(key):
                bits.append(f"{label}: {p[key]}")
        return "; ".join(bits)

    def get_full_history(self) -> dict:
        """Todo el expediente en una sola respuesta: series de laboratorio compactas, informes y clínico."""
        obs = self._observations()
        flag = {"ok": "", "low": "L", "high": "H", "abnormal": "A"}
        series: dict[str, dict] = {}
        for o in obs:
            e = series.setdefault(
                o["analyte_key"],
                {"analyte": self._name(o["analyte_key"]), "unit": o["unit"] or None, "points": []},
            )
            src = self._src("laboratorio", o["document_id"], o["document_title"], o["collected_on"])
            e["points"].append([o["collected_on"], self._value(o), flag.get(o["status"], "?"), src])
            e["reference"] = self._reference(o)
            e["reference_source"] = REFERENCE_SOURCE.get(o.get("ref_source"), "no hay")
        studies = self.list_studies()["studies"]
        for st in studies:
            if st.get("conclusion") and len(st["conclusion"]) > 900:
                st["conclusion"] = (
                    st["conclusion"][:900] + "… (usa get_imaging_report para el texto completo)"
                )
        return {
            "legend": "En lab_series cada punto es [fecha, valor, estado, fuente]; estado: L bajo, H alto, A anormal, "
            "vacío en rango, ? sin referencia.",
            "person": self.get_health_profile(),
            "summary": self.get_summary(),
            "clinical": self.get_clinical_record(),
            "lab_series": series,
            "studies": studies,
        }

    def run(self, name: str, args: dict) -> dict:
        fn = {
            "find_analytes": self.find_analytes,
            "get_results": self.get_results,
            "get_summary": self.get_summary,
            "list_studies": self.list_studies,
            "get_imaging_report": self.get_imaging_report,
            "get_clinical_record": self.get_clinical_record,
            "get_full_history": self.get_full_history,
            "get_health_profile": self.get_health_profile,
        }.get(name)
        if fn is None:
            raise ValueError(f"Herramienta desconocida: {name}")
        return fn(**args)


_CITE = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")


def resolve_citations(answer: str, sources: dict[str, dict]) -> tuple[str, list[dict]]:
    """Convierte [S3] en [1], [2]… por orden de aparición y quita las citas que no existen."""
    order: list[str] = []

    def repl(m: re.Match) -> str:
        ids = [i for i in re.findall(r"S\d+", m.group(1)) if i in sources]
        for i in ids:
            if i not in order:
                order.append(i)
        return "".join(f"[{order.index(i) + 1}]" for i in ids)

    text = _CITE.sub(repl, answer)
    text = re.sub(r"\[\s*S\d+[^\]]*\]", "", text)  # citas mal formadas o inexistentes
    cited = [
        {"n": n, **{k: sources[i][k] for k in ("kind", "document_id", "title", "date")}}
        for n, i in enumerate(order, 1)
    ]
    return re.sub(r"[ \t]{2,}", " ", text).strip(), cited


_CLAIM = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:mg|g|mmol|ng|pg|µ|u/|ui|meq|mmhg|%|x10|10\^|fl|s\b)|\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)


def _has_claims(text: str) -> bool:
    """Valores con unidad o años: lo que sí necesita una fuente (no «B12» ni «hace 5 años»)."""
    return bool(_CLAIM.search(text))


def check_messages(messages: list[dict]) -> list[dict]:
    if not messages or messages[-1].get("role") != "user":
        raise AssistantError(422, "Escribe tu pregunta.")
    if len(messages) > MAX_TURNS:
        raise AssistantError(422, "La conversación es muy larga: empieza una nueva.")
    clean = []
    for m in messages:
        role, text = m.get("role"), str(m.get("content") or "").strip()
        if role not in ("user", "assistant") or not text:
            raise AssistantError(422, "Mensaje inválido.")
        if len(text) > MAX_CHARS:
            raise AssistantError(422, f"Cada mensaje puede tener hasta {MAX_CHARS} caracteres.")
        clean.append({"role": role, "content": text})
    return clean


def ask(
    router: Router,
    db: sqlite3.Connection,
    person: sqlite3.Row,
    names: list[str],
    messages: list[dict],
    today: date | None = None,
    *,
    deep: bool = False,
) -> dict:
    """Responde con el expediente y guías oficiales. `deep` = revisión integral (más razonamiento y búsquedas)."""
    today = today or date.today()
    clean = check_messages(messages)
    box = Toolbox(db, person, names, today)
    system = f"{SYSTEM}\nPersona: {box.person_context()}.\nFecha de hoy: {today.isoformat()}."
    last = None if deep else reviews.latest_done(db, person["id"])
    if (
        last
    ):  # las preguntas se apoyan en la última revisión integral y solo van a los datos cuando hace falta
        system += (
            f"\n\nÚLTIMA REVISIÓN INTEGRAL ({(last['finished_at'] or '')[:10]}), ya hecha sobre TODO el historial. "
            "Úsala como contexto y NO vuelvas a leer todo el expediente (evita get_full_history): consulta solo "
            "las herramientas puntuales que necesites para datos exactos o para algo posterior a esa fecha. "
            "Si la pregunta no se puede contestar con la revisión, usa las herramientas.\n"
            f"{last['content'][:9000]}"
        )
    limits = (
        {
            "max_tokens": 20000,
            "max_rounds": 16,
            "effort": "high",
            "web_search": {"allowed_domains": WEB_DOMAINS, "max_uses": 8},
        }
        if deep
        else {
            "max_tokens": 12000,
            "max_rounds": 10,
            "web_search": {"allowed_domains": WEB_DOMAINS, "max_uses": 3},
        }
    )
    res = router.chat_with_tools("interpret", system, clean, TOOLS, box.run, **limits)
    answer, cited = resolve_citations(res.text, box.sources)
    has_data = _has_claims(answer)
    return {
        "answer": answer,
        "sources": cited,
        "web_sources": list(res.web_sources),
        "web_note": res.web_note,
        "warning": has_data and not cited,  # números sin fuente: que la persona lo verifique
        "usage": {"cost_usd": res.cost_usd, "rounds": res.rounds},
        "used_review": bool(last),
    }
