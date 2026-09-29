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


# ---------- modo orientación: perfil, panorama completo, búsqueda web y revisión integral ----------

from types import SimpleNamespace as NS  # noqa: E402


class Spy(FakeChat):
    """Guarda cómo se le habla al modelo (sistema, herramientas y límites) y usa el panorama completo."""

    def chat_with_tools(self, system, messages, tools, run_tool, **kw):
        self.calls.append({"system": system, "tools": [t["name"] for t in tools], "kw": kw})
        full = run_tool("get_full_history", {})
        self.tool_outputs.append(full)
        sid = full["lab_series"]["glucose"]["points"][-1][3]
        return ChatResult(
            text=f"## Resumen ejecutivo\nLa glucosa está en {full['lab_series']['glucose']['points'][-1][1]} mg/dL [{sid}].",
            provider="fake", model="m", cost_usd=0.5, rounds=2,
            web_sources=({"url": "https://medlineplus.gov/x", "title": "Guía"},),
        )  # fmt: skip


def seeded(tmp_path, fake):
    c = make_client(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    load_glucose(c, me)
    c.put(
        f"/api/people/{me}/health/profile",
        json={"height_cm": "178", "smoking": "Exfumador", "alcohol": "Semanal"},
        headers=H,
    )
    c.post(
        f"/api/people/{me}/health/measurements",
        json={"kind": "weight_kg", "value": "82", "measured_on": "2026-03-01"},
        headers=H,
    )
    return c, me


def test_full_history_gives_everything_with_sources_and_the_system_knows_the_person(tmp_path):
    spy = Spy()
    c, me = seeded(tmp_path, spy)
    r = c.post(
        f"/api/people/{me}/assistant",
        json={"messages": [{"role": "user", "content": "¿Qué me falta revisar?"}]},
        headers=H,
    )
    assert r.status_code == 200
    out = r.json()
    assert out["answer"].endswith("[1].") and out["sources"][0]["title"] == "Perfil 2026"
    assert out["web_sources"] == [{"url": "https://medlineplus.gov/x", "title": "Guía"}]

    call = spy.calls[0]
    assert {"get_full_history", "get_health_profile"} <= set(call["tools"])
    assert (
        "hombre de 3" in call["system"]
        and "talla 178 cm" in call["system"]
        and "peso 82 kg" in call["system"]
    )
    assert "tabaquismo: exfumador" in call["system"] and "IMC 25.9" in call["system"]
    assert (
        "Admin Ejemplo" not in call["system"] and "1990" not in call["system"]
    )  # sin nombre ni fecha de nacimiento
    assert (
        call["kw"]["web_search"]["max_uses"] == 3
        and "medlineplus.gov" in call["kw"]["web_search"]["allowed_domains"]
    )

    full = spy.tool_outputs[0]
    assert full["lab_series"]["glucose"]["points"] == [
        ["2025-03-01", 88, "", full["lab_series"]["glucose"]["points"][0][3]],
        ["2026-03-01", 105, "H", full["lab_series"]["glucose"]["points"][1][3]],
    ]
    assert full["person"]["bmi"] == 25.9 and full["person"]["profile"]["smoking"] == "Exfumador"
    assert full["clinical"]["src"] and len(full["studies"]) == 2  # dos laboratorios
    assert full["lab_series"]["glucose"]["reference"] == "70 a 99"


def test_integral_review_runs_in_background_is_saved_and_can_fail_cleanly(tmp_path):
    import time

    spy = Spy()
    c, me = seeded(tmp_path, spy)
    rid = c.post(f"/api/people/{me}/reviews", headers=H).json()["id"]
    for _ in range(100):
        r = c.get(f"/api/reviews/{rid}").json()
        if r["status"] != "running":
            break
        time.sleep(0.05)
    assert r["status"] == "done" and r["cost_usd"] == 0.5 and "Resumen ejecutivo" in r["content"]
    assert r["sources"][0]["kind"] == "laboratorio" and r["web_sources"][0]["url"].startswith(
        "https://medlineplus.gov"
    )
    kw = spy.calls[0]["kw"]  # la revisión usa el máximo esfuerzo y más búsquedas
    assert kw["effort"] == "high" and kw["max_tokens"] == 16000 and kw["web_search"]["max_uses"] == 8
    assert [x["id"] for x in c.get(f"/api/people/{me}/reviews").json()] == [rid]
    assert (
        c.delete(f"/api/reviews/{rid}", headers=H).status_code == 200
        and c.get(f"/api/reviews/{rid}").status_code == 404
    )

    from house.providers import ProviderError

    def boom(run):
        raise ProviderError("claude: se acabó el saldo de tu cuenta de Anthropic (agrega crédito en Billing)")

    c2 = make_client(tmp_path / "otro", FakeChat(boom))
    c2.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me2 = c2.get("/api/me").json()["id"]
    rid2 = c2.post(f"/api/people/{me2}/reviews", headers=H).json()["id"]
    for _ in range(100):
        r = c2.get(f"/api/reviews/{rid2}").json()
        if r["status"] != "running":
            break
        time.sleep(0.05)
    assert r["status"] == "error" and "saldo" in r["error"]

    plain = TestClient(create_app(tmp_path / "sin", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    assert (
        plain.post(f"/api/people/{plain.get('/api/me').json()['id']}/reviews", headers=H).status_code == 409
    )


def test_web_search_tool_collects_cited_pages_continues_pause_turn_and_falls_back(tmp_path):
    from house.providers.anthropic_provider import AnthropicProvider

    def usage():
        return NS(input_tokens=10, output_tokens=5)

    cite = NS(url="https://uspreventiveservicestaskforce.org/g", title="USPSTF")
    seen_result = NS(url="https://medlineplus.gov/otra", title="Otra")
    pause = NS(
        stop_reason="pause_turn", usage=usage(), content=[NS(type="server_tool_use", name="web_search")]
    )
    final = NS(
        stop_reason="end_turn", usage=usage(),
        content=[NS(type="web_search_tool_result", content=[seen_result]), NS(type="text", text="Listo.", citations=[cite])],
    )  # fmt: skip
    calls = []

    class Client:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                calls.append(kw)
                return pause if len(calls) == 1 else final

    prov = AnthropicProvider("claude", "m", client=Client())
    res = prov.chat_with_tools("s", [{"role": "user", "content": "hola"}], [], lambda *_: {},
                               web_search={"allowed_domains": ["cdc.gov"], "max_uses": 4}, effort="high")  # fmt: skip
    assert res.text == "Listo." and res.rounds == 2
    assert res.web_sources == (
        {"url": "https://uspreventiveservicestaskforce.org/g", "title": "USPSTF"},
    )  # lo citado
    web = calls[0]["tools"][-1]
    assert (
        web["type"].startswith("web_search")
        and web["allowed_domains"] == ["cdc.gov"]
        and web["max_uses"] == 4
    )
    assert calls[0]["output_config"] == {"effort": "high"}
    assert calls[1]["messages"][-1]["role"] == "assistant"  # se le devolvió su turno para que siguiera

    # la cuenta no tiene búsqueda web: se reintenta sin ella y se avisa
    seq = []

    class NoWeb:
        class messages:  # noqa: N801
            @staticmethod
            def create(**kw):
                seq.append(kw)
                if any(t.get("name") == "web_search" for t in kw["tools"]):
                    raise RuntimeError("web_search tool is not enabled for your organization")
                return NS(
                    stop_reason="end_turn",
                    usage=usage(),
                    content=[NS(type="text", text="Sin web.", citations=None)],
                )

    res = AnthropicProvider("claude", "m", client=NoWeb()).chat_with_tools(
        "s", [{"role": "user", "content": "hola"}], [], lambda *_: {}, web_search={"allowed_domains": []}
    )
    assert res.text == "Sin web." and "búsqueda web" in res.web_note and len(seq) == 2
