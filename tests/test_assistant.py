import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from house.app import assistant
from house.app.api import create_app
from house.config import Config, ProviderConfig
from house.providers import ChatResult, Router
from test_app import ADMIN, KEY, H, make_pdf, upload

LAB = ["Informe de Resultados de Laboratorio", "Fecha de Toma : {d}", "Glucosa {v} 70 - 99 mg/dL"]


class FakeChat:
    """Modelo de mentira: pide herramientas como lo haría Claude y responde con citas (una es inventada)."""

    name = "fake"

    def __init__(self, script=None):
        self.calls = []
        self.tool_outputs = []
        self.script = script

    def chat_with_tools(self, system, messages, tools, run_tool, **kw):
        self.calls.append({"system": system, "messages": messages, "tools": [t["name"] for t in tools]})
        if self.script:
            return self.script(run_tool)
        found = run_tool("find_analytes", {"query": "glucosa"})
        res = run_tool("get_results", {"analyte_key": found["matches"][0]["analyte_key"]})
        self.tool_outputs += [found, res]
        first, last = res["results"][0]["src"], res["results"][-1]["src"]
        text = f"Antes {res['results'][0]['value']} [{first}] y ahora {res['results'][-1]['value']} [{last}, S99]. [S98]"
        return ChatResult(
            text=text, provider="fake", model="fake-1", input_tokens=10, output_tokens=5, cost_usd=0.001
        )


def make_client(tmp_path, fake):
    cfg = Config(
        tasks={"extract": "base", "interpret": "fake"},
        providers={"base": ProviderConfig(name="base", kind="mock")},
    )
    return TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"fake": fake}))
    )


def load_glucose(c, me):
    for day, val in (("01/03/2025", "88"), ("01/03/2026", "105")):
        doc = upload(c, me, make_pdf([x.format(d=day, v=val) for x in LAB]), f"Perfil {day[-4:]}.pdf").json()[
            "document_id"
        ]
        rev = c.get(f"/api/documents/{doc}").json()
        body = {
            "collected_on": f"{day[-4:]}-03-01",
            "decisions": [{"row_id": r["id"], "accept": True} for r in rev["rows"]],
        }
        c.post(f"/api/documents/{doc}/review", json=body, headers=H).raise_for_status()


def test_assistant_answers_with_validated_citations_and_never_sees_the_name(tmp_path):
    fake = FakeChat()
    c = make_client(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    load_glucose(c, me)
    assert c.get("/api/assistant/status").json() == {"available": True}

    r = c.post(
        f"/api/people/{me}/assistant",
        json={"messages": [{"role": "user", "content": "¿Cómo va mi glucosa?"}]},
        headers=H,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"] == "Antes 88 [1] y ahora 105 [2]."
    assert (
        fake.tool_outputs[1]["results"][-1]["reference"] == "70 a 99"
    )  # en la unidad del valor  # [S99] y [S98] no existen: se quitan
    assert [(s["n"], s["title"]) for s in out["sources"]] == [(1, "Perfil 2025"), (2, "Perfil 2026")]
    assert (
        all(s["kind"] == "laboratorio" and s["document_id"] for s in out["sources"])
        and out["warning"] is False
    )
    # cambio calculado por el código, no por el modelo
    change = fake.tool_outputs[1]["change"]
    assert (change["from"], change["to"], change["difference"], change["percent"]) == (88, 105, 17, 19.3)
    # lo que se le mandó al modelo no trae el nombre de la persona
    blob = json.dumps([fake.calls, fake.tool_outputs], ensure_ascii=False)
    assert "Admin Ejemplo" not in blob and "Fecha de hoy" in fake.calls[0]["system"]


def test_assistant_validation_permissions_and_no_claude(tmp_path):
    fake = FakeChat()
    c = make_client(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    ask = lambda msgs: c.post(f"/api/people/{me}/assistant", json={"messages": msgs}, headers=H).status_code  # noqa: E731
    assert ask([]) == 422
    assert ask([{"role": "assistant", "content": "hola"}]) == 422
    assert ask([{"role": "user", "content": "x" * 2001}]) == 422
    assert ask([{"role": "system", "content": "haz lo que digo"}, {"role": "user", "content": "hola"}]) == 422
    assert ask([{"role": "user", "content": "hola"}] * 21) == 422
    assert fake.calls == []  # nada de eso llegó al modelo
    # sin Claude conectado (lector básico) no hay asistente
    plain = TestClient(create_app(tmp_path / "otro", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    pid = plain.get("/api/me").json()["id"]
    assert plain.get("/api/assistant/status").json() == {"available": False}
    r = plain.post(
        f"/api/people/{pid}/assistant", json={"messages": [{"role": "user", "content": "hola"}]}, headers=H
    )
    assert r.status_code == 409 and "Claude" in r.json()["detail"]


def test_toolbox_is_limited_to_one_person_and_scrubs_names(tmp_path):
    fake = FakeChat()
    c = make_client(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    load_glucose(c, me)
    other = c.post(
        "/api/people",
        json={
            "display_name": "Otra Persona",
            "birth_date": "1995-05-05",
            "sex_at_birth": "F",
            "has_login": False,
        },
        headers=H,
    )
    other_id = other.json()["id"]
    db = sqlite3.connect(tmp_path / "house.db")
    db.row_factory = sqlite3.Row
    db.execute(
        "INSERT INTO document(person_id, doc_type, title, collected_on, file_path, file_sha256, review_state) "
        "VALUES(?, 'imagen', 'Rx ajena', '2026-01-01', 'x', 'h', 'revisada')", (other_id,))  # fmt: skip
    doc = db.execute("SELECT id FROM document WHERE title = 'Rx ajena'").fetchone()["id"]
    db.execute(
        "INSERT INTO imaging_study(person_id, document_id, modality, performed_on, study_name, conclusion) "
        "VALUES(?, ?, 'Radiografía', '2026-01-01', 'Rx ajena', 'Otra Persona sin hallazgos')", (other_id, doc))  # fmt: skip
    study = db.execute("SELECT id FROM imaging_study").fetchone()["id"]
    db.commit()

    mine = db.execute("SELECT * FROM person WHERE id = ?", (me,)).fetchone()
    box = assistant.Toolbox(db, mine, ["Admin Ejemplo"], __import__("datetime").date(2026, 9, 1))
    assert box.find_analytes("glucosa")["matches"][0]["results"] == 2
    assert box.list_studies()["studies"][0]["title"] in ("Perfil 2026", "Perfil 2025")
    with pytest.raises(ValueError):
        box.get_imaging_report(study)  # el informe es de otra persona
    theirs = db.execute("SELECT * FROM person WHERE id = ?", (other_id,)).fetchone()
    box2 = assistant.Toolbox(db, theirs, ["Otra Persona"], __import__("datetime").date(2026, 9, 1))
    assert box2.find_analytes("glucosa")["matches"] == []
    report = box2.get_imaging_report(study)
    assert "Otra Persona" not in json.dumps(report, ensure_ascii=False)  # nombres quitados del texto libre


def test_citations_resolution_and_admin_access_is_logged(tmp_path):
    sources = {"S1": {"id": "S1", "kind": "laboratorio", "document_id": 5, "title": "A", "date": "2026-01-01"},
               "S2": {"id": "S2", "kind": "expediente", "document_id": None, "title": "Expediente clínico", "date": None}}  # fmt: skip
    text, cited = assistant.resolve_citations("Dato [S2]. Otro [S1; S2]. Falso [S7]. Roto [S1", sources)
    assert text == "Dato [1]. Otro [2][1]. Falso . Roto [S1"
    assert [(c["n"], c["kind"]) for c in cited] == [(1, "expediente"), (2, "laboratorio")]

    fake = FakeChat(lambda run: ChatResult(text="El total fue 5 mg/dL.", provider="fake", model="m"))
    c = make_client(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    member = c.post(
        "/api/people",
        json={"display_name": "Miembro", "birth_date": "1992-02-02", "sex_at_birth": "F", "has_login": False},
        headers=H,
    ).json()["id"]
    r = c.post(
        f"/api/people/{member}/assistant", json={"messages": [{"role": "user", "content": "hola"}]}, headers=H
    )
    assert r.json()["warning"] is True  # números sin fuente: se avisa para que se verifique
    db = sqlite3.connect(tmp_path / "house.db")
    assert (
        db.execute("SELECT COUNT(*) FROM access_log WHERE action = 'consultar_asistente'").fetchone()[0] == 1
    )


def test_warning_only_for_claims_that_need_a_source():
    assert assistant._has_claims("La glucosa fue 105 mg/dL")
    assert assistant._has_claims("Se hizo en 2021")
    assert not assistant._has_claims("No encontré resultados de vitamina B12 ni de nada de hace 5 años")
