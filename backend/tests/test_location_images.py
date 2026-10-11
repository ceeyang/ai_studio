"""场景图（B）与镜头构图参考图（G）：出图提示词、候选与选定、参考图打包顺序、截断、首帧过期。"""
from dataclasses import replace

import pytest

from backend.app.views import shot_detail
from backend.domain.characters import portrait_of, set_portrait
from backend.domain.locations import location_image_of, set_location_image
from backend.domain.models import Character, Location, Project, SceneBlock, Shot, StyleBible
from backend.domain.refs import (MAX_SHOT_REFS, add_shot_ref, remove_shot_ref, shot_ref_images,
                                 shot_refs, split_refs)
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.locations import location_prompt, run_location_image, time_phrase
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

GARAGE = ("A cramped auto repair garage with oil-stained concrete floor. Tools hang on the pegboard "
          "walls under a bare bulb.")
ANCHOR = "Cinematic still, 35mm lens, Kodak film grain, rainy night street, neon reflections."


def png(n: int = 1) -> bytes:
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (8 + n, 8), (n * 20 % 255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def setup(tmp_path, db, image_engine="h3_image"):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text=ANCHOR, negative="cartoon")
        loc = Location(project_id=proj_id, name="修车行", time_of_day="夜晚", visual_prompt=GARAGE)
        s.add(loc)
        s.flush()
        blk = SceneBlock(episode_id=ep_id, order=0, location_id=loc.id, text="陈师傅修车")
        chen = Character(project_id=proj_id, name="陈师傅", visual_prompt="a man in his fifties")
        s.add_all([blk, chen])
        s.flush()
        sh = Shot(episode_id=ep_id, order=0, code="S01", scene_block_id=blk.id,
                  image_prompt="medium shot, a mechanic under a car", shot_size="中景",
                  cast=[{"character_id": chen.id, "variation_id": None}])
        s.add(sh)
        s.flush()
        ids = {"project": proj_id, "episode": ep_id, "loc": loc.id, "chen": chen.id, "shot": sh.id}
    return FakeCtx(sv, job_id=21), runner, ids


# ------------------------------------------------------------ 提示词
def test_location_prompt_is_people_free_and_positive():
    loc = Location(name="修车行", time_of_day="夜晚", visual_prompt=GARAGE)
    p = location_prompt(loc, ANCHOR)
    assert p.startswith("Empty cramped auto repair garage with oil-stained concrete floor, unoccupied, night.")
    assert "Tools hang on the pegboard walls" in p
    assert "unoccupied" in p and "empty" in p.lower()
    # 正面写法：不写 no people / without people 这类否定词（cfg 1 下会把人招进画面）
    assert "no people" not in p.lower() and "without" not in p.lower() and "nobody" not in p.lower()
    # 只取风格锚里的成像词：镜头、胶片；不带 rainy night street 这类场景词
    assert "Style: " in p and "Kodak film grain" in p and "neon" not in p
    # 不写「等演员」这类会把人招进画面的词，也不硬说「照片风格」（动画 / 插画预设会打架）
    assert "actor" not in p.lower() and "waiting" not in p.lower()
    assert "Photographic" not in p
    assert "the whole set is visible and still, wide eye-level view of the bare environment" in p


LIVE_ANCHOR = ("Photorealistic live-action cinematic still, shot on 35mm and 50mm prime lenses with "
               "shallow depth of field, subtle fine film grain and gentle halation on highlights, "
               "muted desaturated color grade with cool shadows and restrained warm accents, moderate "
               "contrast with deep but detailed blacks, soft directional light with natural falloff, "
               "natural skin texture, realistic fabric weave and material detail, grounded documentary "
               "realism.")
STAIRWELL = ("A narrow concrete stairwell landing of an old residential building at eleven at night "
             "during heavy rain. Cracked whitewashed walls and a single caged bulb.")


def test_location_prompt_does_not_repeat_time_of_day():
    """ep 8：「…at eleven at night during heavy rain, unoccupied, night.」时段写了两遍。"""
    loc = Location(name="楼梯间", time_of_day="夜晚", visual_prompt=STAIRWELL)
    p = location_prompt(loc, LIVE_ANCHOR)
    assert p.startswith("Empty narrow concrete stairwell landing of an old residential building at "
                        "eleven at night during heavy rain, unoccupied.")
    assert ", night." not in p
    for word in ("Morning", "dusk", "midnight", "daytime", "noon", "evening", "dawn"):
        loc = Location(name="x", time_of_day="夜晚", visual_prompt=f"A quiet street at {word}.")
        assert location_prompt(loc, "").startswith(f"Empty quiet street at {word}, unoccupied.")
    # 地方里没写时段：照常补
    loc = Location(name="x", time_of_day="夜晚", visual_prompt="A quiet street. Wet asphalt.")
    assert location_prompt(loc, "").startswith("Empty quiet street, unoccupied, night.")


def test_location_prompt_style_drops_skin_clauses():
    """空场景用不上「natural skin texture」：风格句里去掉写皮肤的分句，其余成像词保留。"""
    p = location_prompt(Location(name="x", visual_prompt=STAIRWELL), LIVE_ANCHOR)
    style = p[p.index("Style: "):]
    assert "skin" not in style.lower()
    assert "subtle fine film grain" in style and "realistic fabric weave and material detail" in style


def test_location_prompt_needs_english():
    with pytest.raises(JobError, match="英文画面提示词"):
        location_prompt(Location(name="修车行", visual_prompt="狭窄的修车行"), "")
    assert location_prompt(Location(name="Harbor pier", visual_prompt=""), "").startswith(
        "Empty Harbor pier, unoccupied.")


def test_time_phrase():
    assert time_phrase("黄昏") == "dusk" and time_phrase("深夜") == "late night"
    assert time_phrase("Late Afternoon") == "late afternoon" and time_phrase("某时") == ""


# ------------------------------------------------------------ 出图与候选
def test_location_image_job_creates_and_selects_first(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    first = run_location_image(ctx, {"location_id": ids["loc"]})["candidate_ids"][0]
    call = runner.calls[0]
    assert call.engine == "zimage" and call.meta["prompt"].startswith("Empty cramped auto repair garage")
    assert "people" in call.meta["negative_ignored"]          # 负面词照记（zimage 不吃）
    assert call.workflow["10"]["inputs"]["filename_prefix"] == f"studio/demo/location_{ids['loc']}"
    ctx.job_id += 1
    second = run_location_image(ctx, {"location_id": ids["loc"], "engine": "zimage"})["candidate_ids"][0]
    with db.session() as s:
        sel = location_image_of(s, ids["loc"])
        assert sel.id == first and second != first             # 第二张不抢选定（会让场景里的镜头过期）
        assert sel.file_path.startswith("demo/media/location/")


def test_location_step_setting_picks_engine(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    ctx.services.settings.save({"step:location": "krea2"})
    run_location_image(ctx, {"location_id": ids["loc"]})
    assert runner.calls[0].engine == "krea2"


def test_location_image_goes_stale_when_setting_changes(tmp_path, db):
    """场景的英文画面提示词 / 名字 / 时段改了：选定的 AI 场景图按旧设定出的，标过期；上传的不标。"""
    from backend.app.routes.locations import location_out

    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    run_location_image(ctx, {"location_id": ids["loc"]})

    def out():
        with db.session() as s:
            return location_out(s, s.get(Location, ids["loc"]))

    assert out().image_stale is False
    for field, value in (("visual_prompt", GARAGE + " A red toolbox."), ("time_of_day", "黄昏"),
                         ("name", "修车铺")):
        with db.session() as s:
            loc = s.get(Location, ids["loc"])
            old = getattr(loc, field)
            setattr(loc, field, value)
        assert out().image_stale is True, field
        with db.session() as s:
            setattr(s.get(Location, ids["loc"]), field, old)
        assert out().image_stale is False
    # 氛围不进出图提示词，改了不算
    with db.session() as s:
        s.get(Location, ids["loc"]).atmosphere = "压抑"
    assert out().image_stale is False
    # 按新设定重出一张并选定：不过期
    with db.session() as s:
        s.get(Location, ids["loc"]).visual_prompt = GARAGE + " A red toolbox."
    ctx.job_id += 1
    new = run_location_image(ctx, {"location_id": ids["loc"]})["candidate_ids"][0]
    with db.session() as s:
        from backend.domain.slots import select_candidate
        select_candidate(s, new)
    assert out().image_stale is False
    # 上传的场景图是用户自己定的：改设定不标过期
    _loc_image(ctx.services, ids["loc"], 5)
    with db.session() as s:
        s.get(Location, ids["loc"]).visual_prompt = GARAGE
    assert out().image_stale is False


# ------------------------------------------------------------ 参考图打包
def _portrait(sv, char_id):
    with sv.db.session() as s:
        return set_portrait(s, sv.media, s.get(Character, char_id), png(3), "p.png").id


def _loc_image(sv, loc_id, n=1):
    with sv.db.session() as s:
        return set_location_image(s, sv.media, s.get(Location, loc_id), png(n), "loc.png").id


def _shot_ref(sv, shot_id, n=1):
    with sv.db.session() as s:
        return add_shot_ref(s, sv.media, s.get(Shot, shot_id), "demo", png(n), f"r{n}.jpg").id


def test_pack_order_location_then_shot_refs_then_cast(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    pid = _portrait(sv, ids["chen"])
    with db.session() as s:
        assert [r.label for r in shot_refs(s, s.get(Shot, ids["shot"]))] == ["陈师傅 定妆照"]
    lid = _loc_image(sv, ids["loc"])
    r1, r2 = _shot_ref(sv, ids["shot"], 1), _shot_ref(sv, ids["shot"], 2)
    with db.session() as s:
        shot = s.get(Shot, ids["shot"])
        refs = shot_refs(s, shot)
        assert [r.candidate_id for r in refs] == [lid, r1, r2, pid]
        assert [r.label for r in refs] == ["场景图·修车行", "构图参考 1", "构图参考 2", "陈师傅 定妆照"]
        # 视频只带出场角色
        assert [r.candidate_id for r in shot_refs(s, shot, video=True)] == [pid]
        # 截断时先保住定妆照，剩下的名额给场景图；发出去的仍按原顺序
        used, dropped = split_refs(refs, 2)
        assert [r.candidate_id for r in used] == [lid, pid] and [r.label for r in dropped] == [
            "构图参考 1", "构图参考 2"]


def test_shot_refs_max_two_and_remove(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    a = _shot_ref(sv, ids["shot"], 1)
    _shot_ref(sv, ids["shot"], 2)
    with pytest.raises(ValueError, match=f"最多 {MAX_SHOT_REFS} 张"):
        _shot_ref(sv, ids["shot"], 3)
    with db.session() as s:
        with pytest.raises(ValueError, match="只支持"):
            add_shot_ref(s, sv.media, s.get(Shot, ids["shot"]), "demo", png(), "x.gif")
        remove_shot_ref(s, ids["shot"], a)
        assert len(shot_ref_images(s, ids["shot"])) == 1
        with pytest.raises(LookupError):
            remove_shot_ref(s, ids["shot"], a)


def test_keyframe_sends_refs_in_priority_and_logs_dropped(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    eng = sv.engines.image("h3_image")
    eng.caps = replace(eng.caps, max_refs=2)
    _portrait(sv, ids["chen"])
    lid = _loc_image(sv, ids["loc"])
    _shot_ref(sv, ids["shot"], 1)
    _shot_ref(sv, ids["shot"], 2)
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    call = runner.calls[0]
    with db.session() as s:
        loc_path = str(sv.media.abs(location_image_of(s, ids["loc"]).file_path))
        ref1 = str(sv.media.abs(shot_ref_images(s, ids["shot"])[0].file_path))
        portrait = str(sv.media.abs(portrait_of(s, ids["chen"]).file_path))
    # 上限 2 张：定妆照先留，剩下一张给场景图；构图参考两张都丢
    assert call.meta["refs"] == [loc_path, portrait] and ref1 not in call.meta["refs"]
    assert any("已丢弃：构图参考 1" in m for m in ctx.logs)
    assert any("已丢弃：构图参考 2" in m for m in ctx.logs)
    assert not any("已丢弃：陈师傅 定妆照" in m for m in ctx.logs)
    with db.session() as s:
        from backend.domain.slots import selected_candidate
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        assert [r["id"] for r in ff.params["inputs"]["refs"]][0] == lid


def _second_actor(db, ids):
    """给镜头再加一个出场角色小李（排在陈师傅后面）。"""
    with db.session() as s:
        li = Character(project_id=ids["project"], name="小李", visual_prompt="a young apprentice")
        s.add(li)
        s.flush()
        sh = s.get(Shot, ids["shot"])
        sh.cast = [*sh.cast, {"character_id": li.id, "variation_id": None}]
        return li.id


def test_split_refs_keeps_one_portrait_per_actor_first():
    from backend.domain.refs import Ref
    packed = [Ref(1, "l", "场景图·修车行", "location"), Ref(2, "a", "构图参考 1", "shot_ref"),
              Ref(3, "b", "构图参考 2", "shot_ref"), Ref(4, "p", "陈师傅 定妆照", "portrait"),
              Ref(5, "q", "小李 定妆照", "portrait"), Ref(6, "c", "陈师傅 三视图·正面", "turnaround")]
    # gemini 网页最多 3 张：两张定妆照 + 场景图
    used, dropped = split_refs(packed, 3)
    assert [r.candidate_id for r in used] == [1, 4, 5]
    assert [r.candidate_id for r in dropped] == [2, 3, 6]
    # 4 张：再补一张构图参考；6 张全带；三视图格排在构图参考后面
    assert [r.candidate_id for r in split_refs(packed, 4)[0]] == [1, 2, 4, 5]
    assert [r.candidate_id for r in split_refs(packed, 5)[0]] == [1, 2, 3, 4, 5]
    assert split_refs(packed, 9) == (packed, [])
    # 角色比名额多：按出场顺序留前面的人，场景图也让位
    used, dropped = split_refs(packed, 1)
    assert [r.candidate_id for r in used] == [4] and 5 in [r.candidate_id for r in dropped]
    assert split_refs(packed, 0) == ([], packed)


def test_keyframe_gemini_limit_keeps_both_faces(tmp_path, db):
    from backend.engines.browser.engines import GeminiImageEngine
    assert GeminiImageEngine.caps.max_refs == 3
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    eng = sv.engines.image("h3_image")
    eng.caps = replace(eng.caps, max_refs=3)          # 和 gemini 网页一样最多 3 张
    li = _second_actor(db, ids)
    p1, p2 = _portrait(sv, ids["chen"]), _portrait(sv, li)
    lid = _loc_image(sv, ids["loc"])
    _shot_ref(sv, ids["shot"], 1)
    _shot_ref(sv, ids["shot"], 2)
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        from backend.domain.slots import selected_candidate
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        assert [r["id"] for r in ff.params["inputs"]["refs"]] == [lid, p1, p2]
        assert [r.get("kind") for r in ff.params["inputs"]["refs"]] == ["location", "portrait", "portrait"]
        params = ff.params
    assert len(runner.calls[0].meta["refs"]) == 3
    assert any("已丢弃：构图参考 1" in m for m in ctx.logs)
    assert not any("没带上" in m for m in ctx.logs)
    from backend.pipeline.prompting import first_frame_locked_face
    assert first_frame_locked_face(params, 3)
    with db.session() as s:
        shot = s.get(Shot, ids["shot"])
        proj = s.get(Project, ids["project"])
        d = shot_detail(s, sv, shot, proj, proj.style_bible)
    assert d.first_frame_portraits_dropped == []
    assert not next(x for x in d.slots if x.kind == "first_frame").stale


def test_keyframe_logs_and_reports_dropped_portraits(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    eng = sv.engines.image("h3_image")
    eng.caps = replace(eng.caps, max_refs=1)
    li = _second_actor(db, ids)
    _portrait(sv, ids["chen"])
    _portrait(sv, li)
    _loc_image(sv, ids["loc"])
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    assert any("小李 定妆照没带上" in m for m in ctx.logs)
    with db.session() as s:
        shot = s.get(Shot, ids["shot"])
        proj = s.get(Project, ids["project"])
        d = shot_detail(s, sv, shot, proj, proj.style_bible)
    assert d.first_frame_portraits_dropped == ["小李 定妆照"]


def test_locked_face_needs_a_face_ref():
    from backend.pipeline.prompting import first_frame_locked_face
    only_scene = {"inputs": {"refs": [{"id": 1, "label": "场景图·修车行", "kind": "location"},
                                      {"id": 2, "label": "构图参考 1", "kind": "shot_ref"}]}}
    assert not first_frame_locked_face(only_scene, 3)
    assert first_frame_locked_face({"inputs": {"refs": [{"id": 3, "label": "陈师傅 定妆照",
                                                          "kind": "portrait"}]}}, 3)
    # 老记录没有 kind：按 label 认
    assert not first_frame_locked_face({"inputs": {"refs": [{"id": 1, "label": "场景图·修车行"}]}}, 3)
    assert first_frame_locked_face({"inputs": {"refs": [{"id": 4, "label": "陈师傅 三视图·正面"}]}}, 3)
    assert not first_frame_locked_face({"inputs": {"refs": []}}, 3)


def test_location_image_selection_makes_first_frame_stale(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"])
    run_keyframe(ctx, {"shot_id": ids["shot"]})

    def detail():
        with db.session() as s:
            shot = s.get(Shot, ids["shot"])
            proj = s.get(Project, ids["project"])
            return shot_detail(s, sv, shot, proj, proj.style_bible)

    ff = next(x for x in detail().slots if x.kind == "first_frame")
    assert not ff.stale
    _loc_image(sv, ids["loc"], 1)
    ff = next(x for x in detail().slots if x.kind == "first_frame")
    assert ff.stale and ff.stale_reason == "加了 场景图·修车行"
    ctx.job_id += 1
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    assert not next(x for x in detail().slots if x.kind == "first_frame").stale
    # 换一张场景图（选定的候选 id 变了）：又过期
    _loc_image(sv, ids["loc"], 2)
    d = detail()
    ff = next(x for x in d.slots if x.kind == "first_frame")
    assert ff.stale and ff.stale_reason == "场景图·修车行换了"
    # 构图参考图槽位不混进 slots，单独给出；引擎上限给出
    _shot_ref(sv, ids["shot"])
    d = detail()
    assert [x.kind for x in d.slots] == ["first_frame"] and len(d.shot_refs) == 1
    assert d.image_max_refs == 9 and d.continue_prev is None


def test_zimage_reports_unused_refs(tmp_path, db):
    ctx, _, ids = setup(tmp_path, db, image_engine="zimage")
    sv = ctx.services
    _loc_image(sv, ids["loc"])
    _shot_ref(sv, ids["shot"])
    with db.session() as s:
        shot = s.get(Shot, ids["shot"])
        proj = s.get(Project, ids["project"])
        d = shot_detail(s, sv, shot, proj, proj.style_bible)
    assert d.image_max_refs == 0
    assert d.first_frame_refs_unused == ["场景图·修车行", "构图参考 1"]


# ------------------------------------------------------------ API
def test_location_api_draw_upload_use(api, tmp_path):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    sv = api.app.state.services
    with sv.db.session() as s:
        pid = s.query(Project).filter_by(slug="demo").one().id
        loc = Location(project_id=pid, name="修车行", visual_prompt=GARAGE)
        bad = Location(project_id=pid, name="天台", visual_prompt="")
        s.add_all([loc, bad])
        s.flush()
        lid, bad_id = loc.id, bad.id
    r = api.post(f"/api/locations/{lid}/images/draw", json={"count": 2})
    assert r.status_code == 200 and len(r.json()["job_ids"]) == 2
    jobs = api.get("/api/jobs").json()
    assert {j["type"] for j in jobs} == {"image.location"}
    assert api.post(f"/api/locations/{bad_id}/images/draw", json={}).status_code == 400
    rows = api.get("/api/projects/demo/locations").json()
    row = next(x for x in rows if x["id"] == lid)
    assert row["image_url"] is None and len(row["active_jobs"]) == 2
    up = api.post(f"/api/locations/{lid}/image", files={"file": ("a.png", png(1), "image/png")})
    assert up.status_code == 200
    first = up.json()["image_candidate_id"]
    assert up.json()["image_url"].startswith("/media/demo/media/location/")
    up2 = api.post(f"/api/locations/{lid}/image", files={"file": ("b.png", png(2), "image/png")}).json()
    assert up2["image_candidate_id"] != first and len(up2["images"]) == 2
    back = api.post(f"/api/locations/{lid}/images/{first}/use")
    assert back.status_code == 200 and back.json()["image_candidate_id"] == first
    assert api.post(f"/api/locations/{bad_id}/images/{first}/use").status_code == 404
    assert api.post(f"/api/locations/{lid}/image", files={"file": ("a.gif", b"x", "image/gif")}).status_code == 400
    patched = api.patch(f"/api/locations/{lid}", json={"time_of_day": "黄昏"}).json()
    assert patched["time_of_day"] == "黄昏" and patched["image_candidate_id"] == first


def test_shot_refs_api(api):
    api.post("/api/projects", json={"title": "提词员", "slug": "demo"})
    ep = api.post("/api/projects/demo/episodes", json={"number": 1, "title": "t"}).json()
    shot = api.post(f"/api/episodes/{ep['id']}/shots", json={}).json()
    sid = shot["id"]
    assert shot["shot_refs"] == []
    r = api.post(f"/api/shots/{sid}/refs", files={"file": ("a.png", png(1), "image/png")})
    assert r.status_code == 200 and len(r.json()["shot_refs"]) == 1
    api.post(f"/api/shots/{sid}/refs", files={"file": ("b.png", png(2), "image/png")})
    third = api.post(f"/api/shots/{sid}/refs", files={"file": ("c.png", png(3), "image/png")})
    assert third.status_code == 400 and "最多 2 张" in third.json()["detail"]
    cid = r.json()["shot_refs"][0]["id"]
    left = api.delete(f"/api/shots/{sid}/refs/{cid}").json()
    assert [c["id"] for c in left["shot_refs"]] != [cid] and len(left["shot_refs"]) == 1
    assert api.delete(f"/api/shots/{sid}/refs/{cid}").status_code == 404
    assert all(sl["kind"] != "shot_ref" for sl in left["slots"])
