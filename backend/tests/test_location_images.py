"""场景图（B）与镜头构图参考图（G）：出图提示词、候选与选定、参考图打包顺序、截断、首帧过期。"""
from dataclasses import replace

import pytest

from backend.app.views import shot_detail
from backend.domain.characters import set_portrait
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
    assert "Photographic style:" in p and "Kodak film grain" in p and "neon" not in p


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
        used, dropped = split_refs(refs, 2)
        assert [r.candidate_id for r in used] == [lid, r1] and [r.label for r in dropped] == [
            "构图参考 2", "陈师傅 定妆照"]


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
    assert call.meta["refs"] == [loc_path, ref1]
    assert any("已丢弃：构图参考 2" in m for m in ctx.logs)
    assert any("已丢弃：陈师傅 定妆照" in m for m in ctx.logs)
    with db.session() as s:
        from backend.domain.slots import selected_candidate
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        assert [r["id"] for r in ff.params["inputs"]["refs"]][0] == lid


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
