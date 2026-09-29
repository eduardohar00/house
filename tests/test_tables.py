import base64

from fastapi.testclient import TestClient

from house.app import tables
from house.app.api import create_app
from house.config import Config
from house.providers import LLMResponse, Router
from house.providers.anthropic_provider import AnthropicProvider
from house.providers.base import LLMRequest
from test_app import ADMIN, KEY, H, make_pdf, upload
from test_imaging import REPORT

TABLE = {
    "tables": [
        {
            "caption": " Batería  Adultos ",
            "columns": ["#", "Extracto", "Grado"],
            "rows": [
                ["1", "Dermatophagoides", "4+"],
                ["2", "Blomia"],
                [None, None, None],
                ["3", "Ambrosia", "3+", "sobra"],
            ],
        },
        {"caption": None, "columns": [], "rows": [["x"]]},
    ],
    "notes": "Grado según el salino",
}


class FakeVision:
    name = "fake"

    def __init__(self):
        self.requests = []

    def complete_json(self, req):
        self.requests.append(req)
        return LLMResponse(data=TABLE, provider="fake", model="m")


def make(tmp_path, fake):
    cfg = Config(tasks={"extract": "fake"}, providers={})
    return TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"fake": fake}))
    )


def test_clean_fits_rows_to_the_columns_and_drops_junk():
    out = tables.clean(TABLE)
    assert (
        out["notes"] == "Grado según el salino" and len(out["tables"]) == 1
    )  # la tabla sin columnas se descarta
    t = out["tables"][0]
    assert t["caption"] == "Batería Adultos"
    assert t["rows"] == [["1", "Dermatophagoides", "4+"], ["2", "Blomia", None], ["3", "Ambrosia", "3+"]]
    assert tables.clean({"tables": [], "notes": None}) == {"tables": [], "notes": None}


def test_anthropic_request_carries_the_images_as_base64_blocks():
    prov = AnthropicProvider("claude", "m", client=object())
    req = LLMRequest(
        task="extract", system="s", user="mira", schema={}, images=(b"\x89PNG-uno", b"\x89PNG-dos")
    )
    content = prov.build_kwargs(req)["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"] and content[2]["text"] == "mira"
    assert base64.b64decode(content[0]["source"]["data"]) == b"\x89PNG-uno"
    assert (
        prov.build_kwargs(LLMRequest(task="extract", system="s", user="hola", schema={}))["messages"][0][
            "content"
        ]
        == "hola"
    )


def test_read_tables_endpoint_sends_page_images_and_stores_the_result(tmp_path):
    fake = FakeVision()
    c = make(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    doc = upload(c, me, make_pdf(REPORT.splitlines()), "Rx torax.pdf").json()["document_id"]
    dec = [{"position": 0, "accept": True, "performed_on": "2026-02-06", "study_name": "Pruebas cutáneas"}]
    c.post(f"/api/documents/{doc}/review-imaging", json={"decisions": dec}, headers=H).raise_for_status()
    study = c.get(f"/api/people/{me}/imaging").json()[0]
    assert study["tables"] is None

    r = c.post(f"/api/imaging/{study['id']}/read-tables", headers=H)
    assert r.status_code == 200 and r.json()["tables"][0]["rows"][1] == ["2", "Blomia", None]
    (req,) = fake.requests
    assert len(req.images) == 1 and req.images[0].startswith(b"\x89PNG")  # la página, como imagen
    assert "NO transcribas datos personales" in req.system
    assert c.get(f"/api/people/{me}/imaging").json()[0]["tables"]["notes"] == "Grado según el salino"
    assert c.post("/api/imaging/9999/read-tables", headers=H).status_code == 404


def test_read_tables_needs_claude_and_respects_permissions(tmp_path):
    plain = TestClient(create_app(tmp_path / "a", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = plain.get("/api/me").json()["id"]
    doc = upload(plain, me, make_pdf(REPORT.splitlines()), "Rx torax.pdf").json()["document_id"]
    dec = [{"position": 0, "accept": True, "performed_on": "2026-02-06", "study_name": "Rx"}]
    plain.post(f"/api/documents/{doc}/review-imaging", json={"decisions": dec}, headers=H).raise_for_status()
    study = plain.get(f"/api/people/{me}/imaging").json()[0]["id"]
    r = plain.post(f"/api/imaging/{study}/read-tables", headers=H)
    assert r.status_code == 409 and "Claude" in r.json()["detail"]
