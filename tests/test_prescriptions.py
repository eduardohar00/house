import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from house.app import ingest, prescriptions
from house.app.api import create_app
from house.config import Config
from house.providers import LLMResponse, Router
from test_app import ADMIN, KEY, H, make_pdf

RX = {
    "prescription_date": "2026-03-12",
    "prescriber": "Dr. Ejemplo",
    "diagnosis": "Gastritis crónica",
    "medications": [
        {"name": "Omeprazol", "dose": "20 mg", "frequency": "cada 24 horas", "duration": "por 14 días",
         "instructions": None, "legible": True},
        {"name": "Sucralfato", "dose": "1 g", "frequency": "cada 8 horas", "duration": None,
         "instructions": "antes de alimentos", "legible": False},
    ],
    "notes": "Control en 2 semanas",
}  # fmt: skip


class FakeRx:
    name = "fake"

    def __init__(self, data=RX):
        self.data, self.requests = data, []

    def complete_json(self, req):
        self.requests.append(req)
        return LLMResponse(data=self.data, provider="fake", model="m")


def make(tmp_path, fake):
    cfg = Config(tasks={"extract": "fake"}, providers={})
    return TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"fake": fake}))
    )


def png(size=(300, 200), color="white"):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def send(c, pid, data, name="receta.png", mime="image/png"):
    return c.post(f"/api/people/{pid}/prescriptions", files={"file": (name, data, mime)}, headers=H)


def test_clean_and_problem_matching():
    out = prescriptions.clean(
        {"prescription_date": "12/03/2026", "prescriber": "  Dr   X ", "diagnosis": "",
         "medications": [{"name": " Omeprazol "}, {"name": ""}, {"dose": "5 mg"}], "notes": None}
    )  # fmt: skip
    assert out["prescription_date"] is None and out["prescriber"] == "Dr X" and out["diagnosis"] is None
    assert [m["name"] for m in out["medications"]] == ["Omeprazol"] and out["medications"][0][
        "legible"
    ] is True
    problems = [{"id": 1, "name": "Prediabetes"}, {"id": 2, "name": "Gastritis"}]
    assert (
        prescriptions.match_problem("Gastritis crónica", problems) == 2
    )  # el diagnóstico contiene el nombre
    assert prescriptions.match_problem("prediabetes", problems) == 1
    assert (
        prescriptions.match_problem("Asma", problems) is None
        and prescriptions.match_problem(None, problems) is None
    )


def test_prescription_images_from_photos_and_pdfs():
    images, kind = ingest.prescription_images(png((4000, 3000)))
    assert kind == "image" and len(images) == 1 and images[0].startswith(b"\xff\xd8\xff")  # JPEG reducido
    assert max(Image.open(io.BytesIO(images[0])).size) <= 2000
    pdf, kind = ingest.prescription_images(make_pdf(["Receta"]))
    assert kind == "pdf" and pdf[0].startswith(b"\x89PNG")
    with pytest.raises(ingest.IngestError) as e:
        ingest.prescription_images(b"no soy una imagen")
    assert e.value.status == 415


def test_upload_review_and_confirm_a_prescription(tmp_path):
    fake = FakeRx()
    c = make(tmp_path, fake)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    problem = c.post(
        f"/api/people/{me}/clinical/problem", json={"name": "Gastritis", "status": "Seguimiento"}, headers=H
    ).json()["id"]
    c.post(f"/api/people/{me}/clinical/medication", json={"name": "Omeprazol", "dose": "10 mg"}, headers=H)

    r = send(c, me, png())
    assert r.status_code == 200 and r.json()["medications"] == 2
    (req,) = fake.requests
    assert len(req.images) == 1 and "NO transcribas otros datos personales" in req.system
    doc = r.json()["document_id"]
    assert send(c, me, png()).status_code == 409  # la misma receta otra vez

    rev = c.get(f"/api/documents/{doc}").json()
    assert rev["document"]["doc_type"] == "receta" and rev["prescription"]["diagnosis"] == "Gastritis crónica"
    assert rev["prescription"]["suggested_problem_id"] == problem and rev["current_medications"] == [
        "Omeprazol"
    ]
    assert c.get(f"/api/documents/{doc}/layout").json() == {"pages": [], "boxes": {}}
    assert c.get(f"/api/documents/{doc}/file").headers["content-type"] == "image/png"

    bad = {"decisions": [{"index": 0, "accept": False}, {"index": 1, "accept": False}]}
    assert c.post(f"/api/documents/{doc}/review-prescription", json=bad, headers=H).status_code == 422
    body = {
        "prescription_date": "2026-03-12", "prescriber": "Dr. Ejemplo", "diagnosis": "Gastritis crónica", "problem_id": problem,
        "decisions": [
            {"index": 0, "accept": True, "name": "Omeprazol", "dose": "20 mg", "frequency": "cada 24 horas", "duration": "por 14 días"},
            {"index": 1, "accept": True, "name": "Sucralfato 1 g", "frequency": "cada 8 horas", "instructions": "antes de alimentos", "active": False},
        ],
    }  # fmt: skip
    assert c.post(f"/api/documents/{doc}/review-prescription", json=body, headers=H).json() == {"saved": 2}
    assert c.post(f"/api/documents/{doc}/review-prescription", json=body, headers=H).status_code == 409

    meds = {m["name"]: m for m in c.get(f"/api/people/{me}/clinical").json()["medications"]}
    om = next(
        m
        for m in c.get(f"/api/people/{me}/clinical").json()["medications"]
        if m["document_id"] == doc and m["name"] == "Omeprazol"
    )
    assert (om["dose"], om["reason"], om["prescriber"], om["since_year"], om["notes"]) == (
        "20 mg cada 24 horas",
        "Gastritis crónica",
        "Dr. Ejemplo",
        "2026",
        "por 14 días",
    )
    suc = meds["Sucralfato 1 g"]
    assert suc["active"] == 0 and suc["notes"] == "antes de alimentos"
    assert om["prescribed_on"] == "2026-03-12" and suc["prescribed_on"] == "2026-03-12"  # fecha de la receta
    manual = next(
        m for m in c.get(f"/api/people/{me}/clinical").json()["medications"] if m["document_id"] is None
    )
    assert manual["prescribed_on"] is None  # el capturado a mano no viene de una receta
    (p,) = c.get(f"/api/people/{me}/clinical").json()["problems"]
    assert sorted(ln["title"] for ln in p["links"]) == [
        "Omeprazol",
        "Sucralfato 1 g",
    ]  # ligados al padecimiento

    (item,) = [d for d in c.get(f"/api/people/{me}/documents").json() if d["id"] == doc]
    assert item["doc_type"] == "receta" and item["results"] == 2 and item["review_state"] == "revisada"
    ev = [e for e in c.get(f"/api/people/{me}/clinical").json()["timeline"] if e["kind"] == "receta"]
    assert ev and ev[0]["date"] == "2026-03-12" and "Sucralfato" in ev[0]["subtitle"]

    # borrar la receta conserva los medicamentos
    c.delete(f"/api/documents/{doc}", headers=H).raise_for_status()
    kept = c.get(f"/api/people/{me}/clinical").json()["medications"]
    assert {"Omeprazol", "Sucralfato 1 g"} <= {m["name"] for m in kept} and all(
        m["document_id"] is None for m in kept
    )


def test_prescription_errors(tmp_path):
    empty = FakeRx({**RX, "medications": []})
    c = make(tmp_path / "a", empty)
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    assert (
        send(c, me, png()).status_code == 422 and c.get(f"/api/people/{me}/documents").json() == []
    )  # nada queda a medias
    assert send(c, me, b"texto", "x.txt", "text/plain").status_code == 415
    plain = TestClient(create_app(tmp_path / "b", key_provider=lambda: KEY))
    plain.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    r = send(plain, plain.get("/api/me").json()["id"], png())
    assert r.status_code == 409 and "Claude" in r.json()["detail"]


def test_ingredient_suggestions_come_from_claude_and_are_not_saved_until_accepted(tmp_path):
    from house.app import drugs

    class Fake:
        name = "fake"
        seen = None

        def complete_json(self, req):
            Fake.seen = req.user
            items = [{"id": int(ln.split(":")[0]), "active_ingredient": "metformina" if "Glucophage" in ln else None}
                     for ln in req.user.splitlines()[1:]]  # fmt: skip
            return LLMResponse(data={"items": items}, provider="fake", model="m")

    cfg = Config(tasks={"extract": "fake", "interpret": "fake"}, providers={})
    c = TestClient(
        create_app(tmp_path, key_provider=lambda: KEY, router=Router(cfg, overrides={"fake": Fake()}))
    )
    c.post("/api/setup", json=ADMIN, headers=H).raise_for_status()
    me = c.get("/api/me").json()["id"]
    a = c.post(f"/api/people/{me}/clinical/medication", json={"brand": "Glucophage"}, headers=H).json()["id"]
    c.post(
        f"/api/people/{me}/clinical/medication", json={"brand": "Marca Rara"}, headers=H
    ).raise_for_status()
    c.post(
        f"/api/people/{me}/clinical/medication",
        json={"active_ingredient": "omeprazol", "brand": "Losec"},
        headers=H,
    ).raise_for_status()

    out = c.post(f"/api/people/{me}/medications/suggest-ingredients", headers=H).json()
    assert out == {"suggestions": [{"id": a, "name": "Glucophage", "active_ingredient": "metformina"}]}
    assert (
        "Losec" not in Fake.seen and "Glucophage" in Fake.seen
    )  # solo van los que no tienen sustancia activa
    assert (
        next(m for m in c.get(f"/api/people/{me}/clinical").json()["medications"] if m["id"] == a)[
            "active_ingredient"
        ]
        is None
    )
    assert drugs.suggest(Router(cfg, overrides={"fake": Fake()}), []) == {}


def test_provider_says_clearly_when_the_anthropic_balance_ran_out():
    import pytest

    from house.providers import LLMRequest, ProviderError
    from house.providers.anthropic_provider import AnthropicProvider

    class Broke:
        class messages:  # noqa: N801 - imita el cliente del SDK
            @staticmethod
            def create(**_):
                raise RuntimeError("Your credit balance is too low to access the Anthropic API.")

    prov = AnthropicProvider("claude", "m", client=Broke())
    with pytest.raises(ProviderError, match="saldo"):
        prov.complete_json(LLMRequest(task="extract", system="s", user="u", schema={}))
    with pytest.raises(ProviderError, match="saldo"):
        prov.chat_with_tools("s", [{"role": "user", "content": "hola"}], [], lambda *_: {})
