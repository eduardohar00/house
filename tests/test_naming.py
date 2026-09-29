import json

from fastapi.testclient import TestClient

from house.app import naming
from house.app.api import create_app
from house.config import Config, ProviderConfig
from house.providers import LLMResponse, Router
from test_app import ADMIN, KEY, H, make_pdf, upload
from test_imaging import REPORT


class Namer:
    name = "namer"

    def __init__(self):
        self.requests = []

    def complete_json(self, req):
        self.requests.append(req)
        items = json.loads(req.user.split("Documentos:\n", 1)[1])
        out = [
            {"id": i["id"], "title": f"2026-02-06 - Nombre limpio {i['id']} (1).pdf",
             "studies": [f"Estudio limpio {k}" for k, _ in enumerate(i["studies"])]}
            for i in items
        ]  # fmt: skip
        return LLMResponse(data={"items": out}, provider="namer", model="m")


def make(tmp_path, namer):
    cfg = Config(
        tasks={"extract": "base", "interpret": "namer"},
        providers={"base": ProviderConfig(name="base", kind="mock")},
    )
    return TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"namer": namer}))
    )


def test_tidy_strips_dates_extensions_and_copy_numbers():
    assert naming.tidy("2026-02-06 - Rx de abdomen (1).pdf", "x") == "Rx de abdomen"
    assert naming.tidy("  perfil   tiroideo ", "x") == "Perfil tiroideo"
    assert naming.tidy("06/02/2026: Biopsias", "x") == "Biopsias"
    assert naming.tidy("", "Original") == "Original" and naming.tidy("1", "Original") == "Original"
    assert len(naming.tidy("a" * 200, "x")) <= 80


def test_new_uploads_get_a_clean_name_and_manual_names_are_never_overwritten(tmp_path):
    namer = Namer()
    c = make(tmp_path, namer)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]

    doc = upload(c, me, make_pdf(REPORT.splitlines()), "2026-02-06 - Rx Torax (1).pdf").json()["document_id"]
    (item,) = c.get(f"/api/people/{me}/documents").json()
    assert (item["title"], item["name_source"]) == (f"Nombre limpio {doc}", "ia")
    assert item["filename"] == "2026-02-06 - Rx Torax (1).pdf"  # el archivo original se conserva
    sent = namer.requests[0].user
    assert (
        "Admin Ejemplo" not in sent and "estructuras" not in sent.lower()
    )  # solo nombres y tipos, no el contenido

    rev = c.get(f"/api/documents/{doc}").json()
    assert [r["study_name"] for r in rev["imaging"]] == [
        "Estudio limpio 0",
        "Estudio limpio 1",
    ]  # borradores con nombre limpio
    dec = [
        {"position": 0, "accept": True, "performed_on": "2026-02-06", "study_name": "Estudio limpio 0"},
        {"position": 1, "accept": True, "performed_on": "2026-02-06", "study_name": "Lo escribí yo"},
    ]
    c.post(f"/api/documents/{doc}/review-imaging", json={"decisions": dec}, headers=H).raise_for_status()
    got = {s["study_name"]: s["name_source"] for s in c.get(f"/api/people/{me}/imaging").json()}
    assert got == {"Estudio limpio 0": None, "Lo escribí yo": "manual"}  # lo corregido a mano queda protegido

    # ordenar de nuevo (botón): ya no hay nada sin nombre; los renombrados a mano no se tocan
    assert c.post(f"/api/people/{me}/names/auto", headers=H).json() == {"renamed": 0, "checked": 0}
    other = upload(
        c,
        me,
        make_pdf(
            ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 90 70 - 99 mg/dL"]
        ),
        "perfil.pdf",
    ).json()["document_id"]
    c.put(f"/api/documents/{other}/title", json={"name": "Mi perfil"}, headers=H).raise_for_status()
    assert c.post(f"/api/people/{me}/names/auto", headers=H).json()["renamed"] == 0
    title = {d["id"]: (d["title"], d["name_source"]) for d in c.get(f"/api/people/{me}/documents").json()}
    assert title[other] == ("Mi perfil", "manual")


def test_bulk_button_names_old_documents_and_needs_claude(tmp_path):
    plain = TestClient(create_app(tmp_path / "a", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    r = plain.post(f"/api/people/{plain.get('/api/me').json()['id']}/names/auto", headers=H)
    assert r.status_code == 409 and "Claude" in r.json()["detail"]

    namer = Namer()
    c = make(tmp_path / "b", namer)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    lab = ["Informe de Resultados de Laboratorio", "Fecha de Toma : 01/03/2026", "Glucosa 90 70 - 99 mg/dL"]
    doc = upload(c, me, make_pdf(lab), "2026-03-01 Perfil (2).pdf").json()["document_id"]
    # simula un documento de antes de esta función: sin marca de nombre
    c.app.state  # noqa: B018 - la base se abre aparte
    import sqlite3

    db = sqlite3.connect(tmp_path / "b" / "house.db")
    db.execute("UPDATE document SET name_source = NULL, title = '2026-03-01 Perfil (2)' WHERE id = ?", (doc,))
    db.commit()
    db.close()
    assert c.post(f"/api/people/{me}/names/auto", headers=H).json() == {"renamed": 1, "checked": 1}
    (item,) = c.get(f"/api/people/{me}/documents").json()
    assert (item["title"], item["name_source"]) == (f"Nombre limpio {doc}", "ia")
    assert "glucosa" in namer.requests[-1].user  # se le dijo qué grupo de análisis trae, no los valores
    assert "90" not in namer.requests[-1].user.replace(str(doc), "")
