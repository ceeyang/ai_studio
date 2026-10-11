"""角色卡：本地 AI 抽定妆照（image.portrait）、切换角色造型、AI 推荐造型和按剧本推荐角色。"""
import pytest

from backend.domain.characters import set_portrait, use_portrait
from backend.domain.models import Character, Episode, Job, SceneBlock, StyleBible
from backend.domain.slots import get_or_create_slot, selected_candidate
from backend.pipeline.characters import run_portrait, run_recommend, run_suggest
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_gateway, make_services, seed_episode

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
LOOK = ("A 45-year-old Chinese male auto mechanic, lean wiry build, short black hair with grey "
        "temples, navy cotton-twill work jacket with orange reflective band")
ANCHOR = "Photorealistic live-action commercial photography, warm golden palette, fine film grain."


def words(n: int, w: str = "detail") -> str:
    return " ".join([w] * n)


def setup(tmp_path, db, *, image_engine="h3_image", llm=None, look=LOOK):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner, llm=llm)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine,
                                  source="陈师傅在修车厂接待小雨，帮她看报价单。")
    with db.session() as s:
        sb = StyleBible(anchor_text=ANCHOR, negative="cartoon", character_rules="只有两个角色")
        s.add(sb)
        s.flush()
        s.get(Episode, ep_id).project.style_bible_id = sb.id
        s.add(SceneBlock(episode_id=ep_id, order=0, text="修车厂里，陈师傅擦着扳手。"))
        ch = Character(project_id=proj_id, name="陈师傅", description="45 岁中国汽修师傅",
                       visual_prompt=look)
        s.add(ch)
        s.flush()
        ids = {"char": ch.id, "project": proj_id, "episode": ep_id}
    return FakeCtx(sv, job_id=11), runner, ids


def portrait_slot(db, char_id):
    with db.session() as s:
        slot = get_or_create_slot(s, "character", char_id, "portrait")
        return slot.selected_candidate_id, [(c.id, dict(c.params)) for c in slot.candidates]


# ---------------------------------------------------------------- image.portrait
def test_portrait_draw_uses_visual_prompt_anchor_and_9_16(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    out = run_portrait(ctx, {"character_id": ids["char"]})
    call = runner.calls[0]
    prompt = call.meta["prompt"]
    assert prompt.startswith(ANCHOR)
    assert LOOK in prompt and "half-body" in prompt and "front" in prompt
    assert "neutral" in prompt.lower() or "grey" in prompt.lower()
    assert call.meta["size"] == [768, 1344]
    assert call.meta["refs"] == []  # 抽新造型不带旧定妆照
    sel, cands = portrait_slot(db, ids["char"])
    assert out["candidate_ids"] == [cands[0][0]]
    assert cands[0][1]["visual_prompt"] == LOOK
    assert cands[0][1]["workflow"].endswith("workflow.api.json")
    assert sel == cands[0][0]  # 之前没有定妆照：自动选定


def test_portrait_draw_does_not_replace_selected(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        up = set_portrait(s, sv.media, s.get(Character, ids["char"]), PNG, "chen.png").id
    run_portrait(ctx, {"character_id": ids["char"], "visual_prompt": "OTHER LOOK " + words(10)})
    ctx.job_id = 12
    run_portrait(ctx, {"character_id": ids["char"]})
    sel, cands = portrait_slot(db, ids["char"])
    assert sel == up and len(cands) == 3
    assert cands[1][1]["visual_prompt"].startswith("OTHER LOOK")
    assert cands[2][1]["visual_prompt"] == LOOK
    # 不同任务种子不同，抽出来的卡不一样
    assert runner.calls[0].meta["seed"] != runner.calls[1].meta["seed"]
    with db.session() as s:
        assert s.get(Character, ids["char"]).visual_prompt == LOOK  # 抽卡不改角色提示词


def test_portrait_draw_needs_prompt_and_character(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, look="")
    with pytest.raises(JobError, match="外貌提示词"):
        run_portrait(ctx, {"character_id": ids["char"]})
    with pytest.raises(JobError, match="角色"):
        run_portrait(ctx, {"character_id": 999})
    assert runner.calls == []


def test_portrait_draw_zimage_gets_negative(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    run_portrait(ctx, {"character_id": ids["char"]})
    assert "cartoon" in runner.calls[0].meta["negative"]
    assert runner.calls[0].engine == "zimage"


# ---------------------------------------------------------------- 切换造型
def test_use_portrait_switches_selection_and_prompt(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        up = set_portrait(s, sv.media, s.get(Character, ids["char"]), PNG, "chen.png").id
    new_look = "NEW LOOK " + words(12)
    drawn = run_portrait(ctx, {"character_id": ids["char"], "visual_prompt": new_look})["candidate_ids"][0]
    with db.session() as s:
        ch = s.get(Character, ids["char"])
        use_portrait(s, ch, drawn)
        assert ch.visual_prompt == new_look
        assert selected_candidate(s, "character", ch.id, "portrait").id == drawn
    # 切回上传的那张：恢复切走时的提示词
    with db.session() as s:
        ch = s.get(Character, ids["char"])
        use_portrait(s, ch, up)
        assert ch.visual_prompt == LOOK
        assert selected_candidate(s, "character", ch.id, "portrait").id == up


def test_use_portrait_rejects_other_characters_candidate(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    with db.session() as s:
        other = Character(project_id=ids["project"], name="小雨", visual_prompt="young woman " + words(10))
        s.add(other)
        s.flush()
        other_id = other.id
    cid = run_portrait(ctx, {"character_id": other_id})["candidate_ids"][0]
    with db.session() as s:
        with pytest.raises(LookupError):
            use_portrait(s, s.get(Character, ids["char"]), cid)
        with pytest.raises(LookupError):
            use_portrait(s, s.get(Character, ids["char"]), 9999)


# ---------------------------------------------------------------- character.suggest
def option(label, n=80):
    return {"label": label, "visual_prompt": words(n, label.lower()), "core_features": "藏青工装"}


def test_suggest_stores_design_options(tmp_path, db):
    gw, prov = make_gateway([{"options": [option("Veteran"), option("Neat"), option("Rugged")]}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    out = run_suggest(ctx, {"character_id": ids["char"], "count": 3, "episode_id": ids["episode"]})
    assert out == {"options": 3}
    user = prov.calls[0]["messages"][0]["content"]
    assert "陈师傅在修车厂接待小雨" in user  # 原文
    assert "修车厂里，陈师傅擦着扳手。" in user  # 段落
    assert "45 岁中国汽修师傅" in user and ANCHOR in user and "3" in user
    with db.session() as s:
        opts = s.get(Character, ids["char"]).design_options
    assert [o["label"] for o in opts] == ["Veteran", "Neat", "Rugged"]
    assert opts[0]["visual_prompt"].startswith("veteran") and opts[0]["core_features"] == "藏青工装"


def test_suggest_retries_on_wrong_count_and_short_prompt(tmp_path, db):
    gw, prov = make_gateway([
        {"options": [option("A"), option("B")]},
        {"options": [option("A", 10), option("B"), option("C")]},
        {"options": [option("A"), option("B"), option("C")]},
    ])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    run_suggest(ctx, {"character_id": ids["char"], "count": 3})  # 不给 episode_id 时读全部剧集
    assert len(prov.calls) == 3
    assert "3" in prov.calls[1]["messages"][-1]["content"]
    assert "英文单词" in prov.calls[2]["messages"][-1]["content"]
    with db.session() as s:
        assert len(s.get(Character, ids["char"]).design_options) == 3


# ---------------------------------------------------------------- character.recommend
def test_recommend_skips_existing_characters(tmp_path, db):
    gw, prov = make_gateway([{"characters": [
        {"name": "陈师傅", "description": "已有", "visual_prompt": words(70), "core_features": ""},
        {"name": "前台小李", "description": "修车厂前台", "visual_prompt": words(70, "clerk"),
         "core_features": "工牌"},
    ]}])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    out = run_recommend(ctx, {"episode_id": ids["episode"], "count": 3})
    assert [c["name"] for c in out["characters"]] == ["前台小李"]
    assert out["characters"][0]["visual_prompt"].startswith("clerk")
    assert "陈师傅" in prov.calls[0]["messages"][0]["content"]  # 已有角色告诉模型
    with db.session() as s:
        assert s.query(Character).count() == 1  # 只推荐，不自动建


# ---------------------------------------------------------------- API
def make_char(api, look="LOOK " + words(10)):
    api.post("/api/projects", json={"title": "demo", "image_engine": "h3_image"})
    api.post("/api/projects/demo/episodes", json={"number": 1, "source_text": "剧本"})
    r = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": look})
    assert r.status_code == 201, r.text
    return r.json()


def jobs(api, job_type):
    with api.app.state.services.db.session() as s:
        return [(j.type, j.owner_type, j.owner_id, dict(j.payload), j.gpu)
                for j in s.query(Job).filter(Job.type == job_type).order_by(Job.id)]


def test_api_draw_submits_portrait_jobs(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 2})
    assert r.status_code == 200, r.text
    assert len(r.json()["job_ids"]) == 2
    js = jobs(api, "image.portrait")
    assert js[0] == ("image.portrait", "character", ch["id"],
                     {"character_id": ch["id"], "engine": "h3_image"}, True)
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw",
                 json={"count": 1, "visual_prompt": "  custom look  "})
    assert jobs(api, "image.portrait")[-1][3] == {"character_id": ch["id"], "engine": "h3_image",
                                                  "visual_prompt": "custom look"}
    assert api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 5}).status_code == 422
    assert api.post("/api/characters/999/portraits/draw", json={"count": 1}).status_code == 404
    # 角色列表带上进行中的抽卡任务
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert [j["type"] for j in listed["active_jobs"]] == ["image.portrait"] * 3


def test_api_draw_without_prompt_is_400(api):
    ch = make_char(api, look="")
    r = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={"count": 1})
    assert r.status_code == 400 and "外貌提示词" in r.json()["detail"]


def test_api_use_portrait_and_listing(api):
    ch = make_char(api)
    sv = api.app.state.services
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "character", ch["id"], "portrait")
        from backend.domain.slots import add_candidate
        a = add_candidate(s, slot, file_path="demo/media/portrait/a.png", engine="h3_image",
                          params={"visual_prompt": "LOOK A"}, input_hash="a", job_id=None).id
        b = add_candidate(s, slot, file_path="demo/media/portrait/b.png", engine="h3_image",
                          params={"visual_prompt": "LOOK B"}, input_hash="b", job_id=None,
                          auto_select=False).id
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert [p["id"] for p in listed["portraits"]] == [a, b]
    assert listed["portraits"][1]["url"] == "/media/demo/media/portrait/b.png"
    assert listed["portrait_candidate_id"] == a and listed["design_options"] == []
    r = api.post(f"/api/characters/{ch['id']}/portraits/{b}/use")
    assert r.status_code == 200, r.text
    assert r.json()["portrait_candidate_id"] == b and r.json()["visual_prompt"] == "LOOK B"
    assert api.post(f"/api/characters/{ch['id']}/portraits/9999/use").status_code == 404


def test_api_suggest_and_recommend_submit_jobs(api):
    ch = make_char(api)
    ep_id = api.get("/api/projects/demo/episodes").json()[0]["id"]
    r = api.post(f"/api/characters/{ch['id']}/suggest", json={"count": 3, "episode_id": ep_id})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.suggest")[0][:4] == (
        "character.suggest", "character", ch["id"],
        {"character_id": ch["id"], "count": 3, "episode_id": ep_id})
    assert api.post(f"/api/characters/{ch['id']}/suggest", json={"count": 9}).status_code == 422
    r = api.post(f"/api/episodes/{ep_id}/characters/recommend", json={})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.recommend")[0][:4] == (
        "character.recommend", "episode", ep_id, {"episode_id": ep_id, "count": 3})
    assert api.post("/api/episodes/999/characters/recommend", json={}).status_code == 404
