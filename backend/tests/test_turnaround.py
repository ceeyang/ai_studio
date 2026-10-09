"""角色三视图：LLM 写 9 格视角描述（character.turnaround_plan），逐格出图（image.turnaround），
首帧 / 视频参考图按机位带上最匹配的一格。"""
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text

from backend.app.views import shot_detail
from backend.domain.characters import set_portrait
from backend.domain.db import MIGRATIONS, Database
from backend.domain.models import Candidate, Character, Episode, Job, Shot, StyleBible
from backend.domain.refs import shot_refs
from backend.domain.slots import get_or_create_slot, select_candidate, selected_candidate
from backend.domain.turnaround import (VIEW_KEYS, VIEW_LABELS, cell_state, slot_kind,
                                       view_for_shot)
from backend.llm.schemas import TurnaroundPlan
from backend.pipeline.characters import run_turnaround, run_turnaround_plan
from backend.pipeline.common import load_episode
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.prompting import keyframe_input_hash
from backend.pipeline.videos import run_video
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_gateway, make_services, seed_episode

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
LOOK = ("A 45-year-old Chinese male auto mechanic, lean wiry build, short black hair with grey "
        "temples, navy cotton-twill work jacket with orange reflective band")
ANCHOR = "Photorealistic live-action commercial photography, warm golden palette, fine film grain."
IMG = ("medium shot, 50mm. a mechanic in a navy jacket looks at a phone in a bright "
       "workshop office")


def words(n: int, w: str = "detail") -> str:
    return " ".join([w] * n)


def plan_json(n: int = 30, skip: str | None = None, extra: dict | None = None) -> dict:
    views = [{"view": k, "description": f"{k} " + words(n)} for k in VIEW_KEYS if k != skip]
    if extra:
        views.append(extra)
    return {"views": views}


def setup(tmp_path, db, *, image_engine="h3_image", video_engine="h3_video", llm=None):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner, llm=llm)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine, video_engine=video_engine)
    with db.session() as s:
        sb = StyleBible(anchor_text=ANCHOR, negative="cartoon", character_rules="只有两个角色")
        s.add(sb)
        s.flush()
        s.get(Episode, ep_id).project.style_bible_id = sb.id
        chen = Character(project_id=proj_id, name="陈师傅", description="45 岁中国汽修师傅",
                         visual_prompt=LOOK, core_features="藏青工装、灰白鬓角")
        yu = Character(project_id=proj_id, name="小雨", visual_prompt="young woman " + words(10))
        s.add_all([chen, yu])
        s.flush()
        sh = Shot(episode_id=ep_id, order=0, code="S01", shot_size="特写", image_prompt=IMG,
                  motion_prompt="he nods",
                  cast=[{"character_id": yu.id, "variation_id": None},
                        {"character_id": chen.id, "variation_id": None}])
        s.add(sh)
        s.flush()
        ids = {"chen": chen.id, "yu": yu.id, "project": proj_id, "episode": ep_id, "shot": sh.id}
    return FakeCtx(sv, job_id=21), runner, ids


def put_portrait(sv, char_id, name="p.png"):
    with sv.db.session() as s:
        c = set_portrait(s, sv.media, s.get(Character, char_id), PNG, name)
        return c.id, sv.media.abs(c.file_path)


def set_plan(db, char_id, plan=None):
    with db.session() as s:
        s.get(Character, char_id).turnaround_plan = plan or {
            k: f"{k} " + words(30) for k in VIEW_KEYS}


def gen_cell(ctx, char_id, view) -> int:
    ctx.job_id += 1
    return run_turnaround(ctx, {"character_id": char_id, "view": view})["candidate_ids"][0]


def stale(sv, shot_id, kind):
    with sv.db.session() as s:
        sh = s.get(Shot, shot_id)
        _, proj = load_episode(s, sh.episode_id)
        d = shot_detail(s, sv, sh, proj, proj.style_bible)
        return next(x.stale for x in d.slots if x.kind == kind)


# ---------------------------------------------------------------- 视角与机位
def test_view_keys_and_labels():
    assert VIEW_KEYS == ("front_full", "front_half", "face_closeup", "left_profile",
                         "right_profile", "three_quarter", "back", "low_angle", "high_angle")
    assert [VIEW_LABELS[k] for k in VIEW_KEYS] == [
        "正面全身", "正面半身", "脸部特写", "左侧", "右侧", "3/4 侧", "背面", "仰视", "俯视"]
    assert slot_kind("back") == "turnaround_back"


@pytest.mark.parametrize("size,extra,expected", [
    ("大特写", "", "face_closeup"),
    ("特写", "", "face_closeup"),
    ("近景", "", "front_half"),
    ("中近景", "", "front_half"),
    ("中景", "", "three_quarter"),
    ("全景", "", "front_full"),
    ("远景", "", "front_full"),
    ("", "", "three_quarter"),
    ("中景", "陈师傅的背影走向车间", "back"),
    ("近景", "shot from behind, he walks away", "back"),
    ("近景", "仰拍，他站在升降机下", "low_angle"),
    ("全景", "low-angle shot of the lift", "low_angle"),
    ("全景", "俯拍车间", "high_angle"),
    ("特写", "侧面，他低头看手机", "left_profile"),
    ("近景", "side profile of the mechanic", "left_profile"),
])
def test_view_for_shot(size, extra, expected):
    sh = Shot(shot_size=size, camera_move="", action=extra, image_prompt="a mechanic")
    assert view_for_shot(sh) == expected


# ---------------------------------------------------------------- character.turnaround_plan
def test_plan_stores_nine_descriptions(tmp_path, db):
    gw, prov = make_gateway([plan_json()])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    out = run_turnaround_plan(ctx, {"character_id": ids["chen"]})
    assert out == {"views": 9}
    user = prov.calls[0]["messages"][0]["content"]
    assert LOOK in user and "藏青工装" in user and ANCHOR in user
    for key, label in VIEW_LABELS.items():
        assert key in prov.calls[0]["system"] and label in prov.calls[0]["system"]
    with db.session() as s:
        plan = s.get(Character, ids["chen"]).turnaround_plan
    assert list(plan) == list(VIEW_KEYS)
    assert plan["back"].startswith("back ")


def test_plan_retries_on_missing_view_or_short_description(tmp_path, db):
    gw, prov = make_gateway([plan_json(skip="back"), plan_json(n=3), plan_json()])
    ctx, runner, ids = setup(tmp_path, db, llm=gw)
    run_turnaround_plan(ctx, {"character_id": ids["chen"]})
    assert len(prov.calls) == 3
    assert "back" in prov.calls[1]["messages"][-1]["content"]
    assert "英文单词" in prov.calls[2]["messages"][-1]["content"]
    with pytest.raises(ValueError, match="重复"):
        TurnaroundPlan.model_validate(plan_json(extra={"view": "front_full", "description": words(30)}))
    with pytest.raises(ValueError):
        TurnaroundPlan.model_validate(plan_json(extra={"view": "top", "description": words(30)}))


def test_plan_needs_visual_prompt(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, llm=make_gateway([])[0])
    with db.session() as s:
        s.get(Character, ids["chen"]).visual_prompt = ""
    with pytest.raises(JobError, match="外貌提示词"):
        run_turnaround_plan(ctx, {"character_id": ids["chen"]})


# ---------------------------------------------------------------- image.turnaround
def test_turnaround_cell_uses_portrait_ref_and_view(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    pid, ppath = put_portrait(sv, ids["chen"])
    set_plan(db, ids["chen"])
    cid = gen_cell(ctx, ids["chen"], "back")
    call = runner.calls[0]
    prompt = call.meta["prompt"]
    assert prompt.startswith(ANCHOR)
    assert "back " + words(30) in prompt and LOOK in prompt
    assert "behind" in prompt.lower() and "grey" in prompt.lower()
    assert call.meta["refs"] == [str(ppath)]   # 定妆照锁脸
    assert call.meta["size"] == [768, 1344]
    with db.session() as s:
        sel = selected_candidate(s, "character", ids["chen"], "turnaround_back")
        assert sel.id == cid and sel.params["view"] == "back"
        assert sel.params["portrait_candidate_id"] == pid
        assert not cell_state(s, s.get(Character, ids["chen"]), "back")[1]


def test_turnaround_regenerate_keeps_selection_unless_stale(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    put_portrait(sv, ids["chen"])
    set_plan(db, ids["chen"])
    first = gen_cell(ctx, ids["chen"], "front_full")
    second = gen_cell(ctx, ids["chen"], "front_full")
    assert runner.calls[0].meta["seed"] != runner.calls[1].meta["seed"]
    with db.session() as s:
        slot = get_or_create_slot(s, "character", ids["chen"], "turnaround_front_full")
        assert [c.id for c in slot.candidates] == [first, second]
        assert slot.selected_candidate_id == first  # 输入没变：不抢选定
    # 换定妆照：这一格过期，新出的自动选定
    put_portrait(sv, ids["chen"], "v2.png")
    with db.session() as s:
        assert cell_state(s, s.get(Character, ids["chen"]), "front_full")[1] is True
    third = gen_cell(ctx, ids["chen"], "front_full")
    with db.session() as s:
        assert selected_candidate(s, "character", ids["chen"], "turnaround_front_full").id == third
        assert cell_state(s, s.get(Character, ids["chen"]), "front_full")[1] is False
    # 改这一格的描述也算过期
    with db.session() as s:
        ch = s.get(Character, ids["chen"])
        ch.turnaround_plan = {**ch.turnaround_plan, "front_full": "new framing " + words(20)}
    with db.session() as s:
        assert cell_state(s, s.get(Character, ids["chen"]), "front_full")[1] is True


def test_turnaround_needs_description_and_portrait(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    set_plan(db, ids["chen"])
    with pytest.raises(JobError, match="定妆照"):
        run_turnaround(ctx, {"character_id": ids["chen"], "view": "back"})
    put_portrait(sv, ids["chen"])
    set_plan(db, ids["chen"], {"front_full": words(20)})
    with pytest.raises(JobError, match="背面"):
        run_turnaround(ctx, {"character_id": ids["chen"], "view": "back"})
    with pytest.raises(JobError, match="视角"):
        run_turnaround(ctx, {"character_id": ids["chen"], "view": "top"})
    assert runner.calls == []


def test_turnaround_zimage_has_no_refs(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    put_portrait(ctx.services, ids["chen"])
    set_plan(db, ids["chen"])
    gen_cell(ctx, ids["chen"], "left_profile")
    assert runner.calls[0].engine == "zimage"
    assert runner.calls[0].dropped_refs == []
    assert "cartoon" in runner.calls[0].meta["negative"]


# ---------------------------------------------------------------- 参考图打包
def test_refs_portraits_first_then_matching_cells(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    yu_pid, _ = put_portrait(sv, ids["yu"], "yu.png")
    chen_pid, _ = put_portrait(sv, ids["chen"], "chen.png")
    set_plan(db, ids["chen"])
    set_plan(db, ids["yu"])
    face = gen_cell(ctx, ids["chen"], "face_closeup")
    gen_cell(ctx, ids["chen"], "back")
    yu_half = gen_cell(ctx, ids["yu"], "front_half")
    with db.session() as s:
        refs = shot_refs(s, s.get(Shot, ids["shot"]))  # 特写 → 脸部特写；小雨没有这一格，退到正面半身
    assert [r.candidate_id for r in refs] == [yu_pid, chen_pid, yu_half, face]
    assert [r.label for r in refs] == ["小雨 定妆照", "陈师傅 定妆照", "小雨 三视图·正面半身",
                                       "陈师傅 三视图·脸部特写"]


def test_refs_skip_stale_cells(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    put_portrait(sv, ids["chen"])
    set_plan(db, ids["chen"])
    gen_cell(ctx, ids["chen"], "face_closeup")
    new_pid, _ = put_portrait(sv, ids["chen"], "v2.png")  # 三视图是按旧定妆照出的
    with db.session() as s:
        assert [r.candidate_id for r in shot_refs(s, s.get(Shot, ids["shot"]))] == [new_pid]


def test_keyframe_packs_cell_and_hash_changes_only_when_cell_used(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    chen_pid, chen_path = put_portrait(sv, ids["chen"])
    with db.session() as s:
        sh, proj = s.get(Shot, ids["shot"]), s.get(Episode, ids["episode"]).project
        old = keyframe_input_hash(sh, proj, proj.style_bible, "h3_image", [chen_pid])
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        # 没有三视图时哈希和以前完全一样：老项目不会因为这个功能变过期
        assert selected_candidate(s, "shot", ids["shot"], "first_frame").input_hash == old
    set_plan(db, ids["chen"])
    gen_cell(ctx, ids["chen"], "back")          # 这一镜是特写，背面格用不上
    assert not stale(sv, ids["shot"], "first_frame")
    face = gen_cell(ctx, ids["chen"], "face_closeup")
    assert stale(sv, ids["shot"], "first_frame")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    call = runner.calls[-1]
    with db.session() as s:
        face_path = sv.media.abs(s.get(Candidate, face).file_path)
    assert call.meta["refs"] == [str(chen_path), str(face_path)]
    assert any("陈师傅 三视图·脸部特写" in m for m in ctx.logs)
    assert not stale(sv, ids["shot"], "first_frame")


def test_video_packs_cell(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    put_portrait(sv, ids["chen"])
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    assert not stale(sv, ids["shot"], "video")
    set_plan(db, ids["chen"])
    gen_cell(ctx, ids["chen"], "face_closeup")
    assert stale(sv, ids["shot"], "video")
    run_video(ctx, {"shot_id": ids["shot"]})
    assert len(runner.calls[-1].meta["refs"]) == 2
    assert not stale(sv, ids["shot"], "video")


def test_refs_truncated_by_engine_and_logged(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        extra = []
        for i in range(8):
            c = Character(project_id=ids["project"], name=f"路人{i}", visual_prompt=words(10))
            s.add(c)
            s.flush()
            extra.append(c.id)
        sh = s.get(Shot, ids["shot"])
        sh.cast = [{"character_id": ids["chen"], "variation_id": None}] + [
            {"character_id": x, "variation_id": None} for x in extra]
    pids = [put_portrait(sv, ids["chen"])[0]] + [put_portrait(sv, x)[0] for x in extra]
    set_plan(db, ids["chen"])
    face = gen_cell(ctx, ids["chen"], "face_closeup")
    with db.session() as s:
        refs = shot_refs(s, s.get(Shot, ids["shot"]))
    assert [r.candidate_id for r in refs] == pids + [face]   # 9 张定妆照在前，三视图格排最后
    run_keyframe(ctx, {"shot_id": ids["shot"]})            # h3_image 上限 9：丢掉三视图格
    assert len(runner.calls[-1].meta["refs"]) == 9
    assert any("丢弃" in m and "陈师傅 三视图·脸部特写" in m for m in ctx.logs)
    with db.session() as s:
        sh, proj = s.get(Shot, ids["shot"]), s.get(Episode, ids["episode"]).project
        assert (selected_candidate(s, "shot", ids["shot"], "first_frame").input_hash
                == keyframe_input_hash(sh, proj, proj.style_bible, "h3_image", pids))


# ---------------------------------------------------------------- 迁移
def test_migration_adds_turnaround_plan(tmp_path):
    d = Database(tmp_path / "m.db")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", d.url)
    command.upgrade(cfg, "b7d2e4f6a8c1")
    with d.engine.begin() as conn:
        conn.execute(text("INSERT INTO projects (slug, title, aspect, image_engine, video_engine, seed, "
                          "negative, created_at) VALUES ('p', 'p', '9:16', 'h3_image', 'h3_video', 1, '', "
                          "'2026-10-09')"))
        conn.execute(text("INSERT INTO characters (project_id, name, description, visual_prompt, "
                          "core_features, design_options) VALUES (1, '陈', '', '', '', '[]')"))
    d.upgrade()
    with d.session() as s:
        assert s.get(Character, 1).turnaround_plan == {}
    command.downgrade(cfg, "b7d2e4f6a8c1")
    d.engine.dispose()


# ---------------------------------------------------------------- API
def make_char(api, look=LOOK):
    api.post("/api/projects", json={"title": "demo", "image_engine": "h3_image"})
    api.post("/api/projects/demo/episodes", json={"number": 1, "source_text": "剧本"})
    r = api.post("/api/projects/demo/characters", json={"name": "陈师傅", "visual_prompt": look})
    assert r.status_code == 201, r.text
    return r.json()


def jobs(api, job_type):
    with api.app.state.services.db.session() as s:
        return [(j.owner_type, j.owner_id, dict(j.payload), j.gpu)
                for j in s.query(Job).filter(Job.type == job_type).order_by(Job.id)]


def test_api_plan_patch_and_listing(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/turnaround/plan", json={})
    assert r.status_code == 200, r.text
    assert jobs(api, "character.turnaround_plan") == [
        ("character", ch["id"], {"character_id": ch["id"]}, False)]
    assert api.post("/api/characters/999/turnaround/plan", json={}).status_code == 404
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert [c["view"] for c in listed["turnaround"]] == list(VIEW_KEYS)
    assert listed["turnaround"][6] == {"view": "back", "label": "背面", "description": "",
                                       "selected_candidate_id": None, "url": None,
                                       "stale": False, "candidates": []}
    assert [j["type"] for j in listed["active_jobs"]] == ["character.turnaround_plan"]
    r = api.patch(f"/api/characters/{ch['id']}/turnaround/plan",
                  json={"views": {"back": "  back view, walking away  ", "low_angle": "looking up"}})
    assert r.status_code == 200, r.text
    cells = {c["view"]: c for c in r.json()["turnaround"]}
    assert cells["back"]["description"] == "back view, walking away"
    assert cells["low_angle"]["description"] == "looking up"
    r = api.patch(f"/api/characters/{ch['id']}/turnaround/plan", json={"views": {"top": "x"}})
    assert r.status_code == 422


def test_api_plan_without_prompt_is_400(api):
    ch = make_char(api, look="")
    r = api.post(f"/api/characters/{ch['id']}/turnaround/plan", json={})
    assert r.status_code == 400 and "外貌提示词" in r.json()["detail"]


def test_api_generate_submits_one_job_per_view(api):
    ch = make_char(api)
    sv = api.app.state.services
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={})
    assert r.status_code == 400 and "视角描述" in r.json()["detail"]
    with sv.db.session() as s:
        c = s.get(Character, ch["id"])
        c.turnaround_plan = {k: f"{k} " + words(20) for k in VIEW_KEYS}
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={})
    assert r.status_code == 400 and "定妆照" in r.json()["detail"]
    with sv.db.session() as s:
        set_portrait(s, sv.media, s.get(Character, ch["id"]), PNG, "chen.png")
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={})
    assert r.status_code == 200, r.text
    assert len(r.json()["job_ids"]) == 9
    js = jobs(api, "image.turnaround")
    assert [j[2]["view"] for j in js] == list(VIEW_KEYS)
    assert js[0] == ("character", ch["id"], {"character_id": ch["id"], "view": "front_full"}, True)
    r = api.post(f"/api/characters/{ch['id']}/turnaround/generate", json={"views": ["back", "low_angle"]})
    assert len(r.json()["job_ids"]) == 2
    assert api.post(f"/api/characters/{ch['id']}/turnaround/generate",
                    json={"views": ["top"]}).status_code == 422
    listed = api.get("/api/projects/demo/characters").json()[0]
    running = [j for j in listed["active_jobs"] if j["type"] == "image.turnaround"]
    assert [j["view"] for j in running][-2:] == ["back", "low_angle"]


def test_api_listing_shows_cells_and_stale(api):
    ch = make_char(api)
    sv = api.app.state.services
    with sv.db.session() as s:
        c = s.get(Character, ch["id"])
        c.turnaround_plan = {k: f"{k} " + words(20) for k in VIEW_KEYS}
        set_portrait(s, sv.media, c, PNG, "chen.png")
    ctx = FakeCtx(sv, job_id=50)
    sv.comfy = FakeRunner()
    cid = run_turnaround(ctx, {"character_id": ch["id"], "view": "back"})["candidate_ids"][0]
    cells = {c["view"]: c for c in api.get("/api/projects/demo/characters").json()[0]["turnaround"]}
    assert cells["back"]["selected_candidate_id"] == cid and cells["back"]["stale"] is False
    assert cells["back"]["url"].startswith("/media/") and len(cells["back"]["candidates"]) == 1
    with sv.db.session() as s:
        set_portrait(s, sv.media, s.get(Character, ch["id"]), PNG, "chen2.png")
    cells = {c["view"]: c for c in api.get("/api/projects/demo/characters").json()[0]["turnaround"]}
    assert cells["back"]["stale"] is True
    assert cells["front_full"]["stale"] is False  # 没出过图不算过期
