"""后期 API：片尾卡、字幕翻译任务、带导出选项的 run、角色音色、对白高棉语字段。"""
from sqlalchemy import select

from backend.domain.models import Job, Shot


def setup(api, dialogue=None):
    api.post("/api/projects", json={"title": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    sh = api.post(f"/api/episodes/{ep['id']}/shots", json={"dialogue": dialogue or [
        {"speaker": "陈师傅", "zh": "能修的，我们绝对不换。", "start": 1.4, "end": 3.8}]}).json()
    return ep, sh


def jobs(api):
    with api.app.state.services.db.session() as s:
        return [(j.type, j.payload, j.depends_on) for j in s.scalars(select(Job).order_by(Job.id))]


CARD = {"seconds": 3, "background": "#0b1a33", "lines": [
    {"text": "កម្ពុជារថយន្តលេខរៀងទី1", "font": "Khmer UI", "size": 92, "bold": True},
    {"text": "", "size": 24},
    {"text": "Telegram 0318891666", "size": 60}]}


def test_end_card_roundtrip_and_validation(api):
    setup(api)
    assert api.get("/api/projects/demo/end-card").json() is None
    r = api.put("/api/projects/demo/end-card", json=CARD)
    assert r.status_code == 200
    got = api.get("/api/projects/demo/end-card").json()
    assert got["lines"][0] == CARD["lines"][0]
    assert got["lines"][1] == {"text": "", "font": None, "size": 24, "bold": False}
    assert api.put("/api/projects/demo/end-card", json={**CARD, "seconds": 30}).status_code == 422
    assert api.put("/api/projects/demo/end-card",
                   json={**CARD, "lines": [{"text": "x", "size": 500}]}).status_code == 422
    assert api.put("/api/projects/demo/end-card",
                   json={**CARD, "background": "navy"}).status_code == 422
    assert api.put("/api/projects/nope/end-card", json=CARD).status_code == 404


def test_translate_endpoint_submits_cpu_job(api):
    ep, _ = setup(api)
    r = api.post(f"/api/episodes/{ep['id']}/subtitles/translate",
                 json={"force": True, "glossary": {"柬埔寨1号铭车": "កម្ពុជារថយន្តលេខរៀងទី1"}})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 1
    (jtype, payload, _), = jobs(api)
    assert jtype == "subtitles.translate"
    assert payload == {"episode_id": ep["id"], "force": True,
                       "glossary": {"柬埔寨1号铭车": "កម្ពុជារថយន្តលេខរៀងទី1"}}
    assert api.post("/api/episodes/999/subtitles/translate", json={}).status_code == 404


def test_run_export_with_options_chains_translate_and_dub(api):
    ep, _ = setup(api)
    chars = api.post("/api/projects/demo/characters", json={"name": "陈师傅"}).json()
    body = {"stage": "export", "export": {"subtitles": "zh+km", "dub": "km", "end_card": True}}
    r = api.post(f"/api/episodes/{ep['id']}/run", json=body)
    assert r.status_code == 400 and "陈师傅" in r.json()["detail"]      # 还没有音色
    c = api.patch(f"/api/characters/{chars['id']}", json={"voice": "km-KH-PisethNeural"}).json()
    assert c["voice"] == "km-KH-PisethNeural"
    r = api.post(f"/api/episodes/{ep['id']}/run", json=body)
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 3
    assert [j[0] for j in jobs(api)] == ["subtitles.translate", "post.dub", "export.episode"]
    assert jobs(api)[-1][1]["options"] == {"subtitles": "zh+km", "dub": "km", "end_card": True,
                                           "trim": True}
    bad = api.post(f"/api/episodes/{ep['id']}/run", json={"stage": "export", "export": {"dub": "fr"}})
    assert bad.status_code == 422


def test_dialogue_km_fields_patchable(api):
    ep, sh = setup(api)
    dlg = [{**sh["dialogue"][0], "km": "អ្វីដែលជួសជុលបាន យើងមិនដូរឡើយ។", "km_review": "checked"}]
    out = api.patch(f"/api/shots/{sh['id']}", json={"dialogue": dlg}).json()
    assert out["dialogue"][0]["km"] == dlg[0]["km"] and out["dialogue"][0]["km_review"] == "checked"
    with api.app.state.services.db.session() as s:
        assert s.get(Shot, sh["id"]).dialogue[0]["km_review"] == "checked"
    bad = api.patch(f"/api/shots/{sh['id']}", json={"dialogue": [{"zh": "x", "start": 2, "end": 1}]})
    assert bad.status_code == 422
