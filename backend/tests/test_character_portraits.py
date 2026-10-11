"""角色定妆照：上传/指定本地文件、新建角色、首帧和视频按在场角色带参考图。"""
import json

import pytest
from typer.testing import CliRunner

import cli.studio as studio
from backend.app.views import shot_detail
from backend.domain.models import Character, Project, Shot, Slot
from backend.domain.slots import selected_candidate
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.videos import run_video
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
IMG = ("medium close-up, 85mm. a mechanic in a navy jacket looks at a phone in a bright "
       "workshop office")


# ---------------------------------------------------------------- API
def make_char(api, name="陈师傅"):
    api.post("/api/projects", json={"title": "demo"})
    r = api.post("/api/projects/demo/characters",
                 json={"name": name, "description": "45 岁中国师傅", "visual_prompt": "LOOK"})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_character(api):
    ch = make_char(api)
    assert ch["name"] == "陈师傅" and ch["visual_prompt"] == "LOOK"
    assert ch["portrait_url"] is None and ch["portrait_candidate_id"] is None
    r = api.post("/api/projects/demo/characters", json={"name": "陈师傅"})
    assert r.status_code == 409
    assert api.post("/api/projects/demo/characters", json={"name": ""}).status_code == 422
    assert api.post("/api/projects/nope/characters", json={"name": "x"}).status_code == 404
    assert [c["name"] for c in api.get("/api/projects/demo/characters").json()] == ["陈师傅"]


def test_upload_portrait_selects_new_candidate(api):
    ch = make_char(api)
    r = api.post(f"/api/characters/{ch['id']}/portrait",
                 files={"file": ("chen.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["portrait_url"].startswith("/media/demo/media/portrait/")
    assert api.get(out["portrait_url"]).content == PNG
    first = out["portrait_candidate_id"]
    r = api.post(f"/api/characters/{ch['id']}/portrait",
                 files={"file": ("chen2.jpg", b"jpg", "image/jpeg")})
    second = r.json()["portrait_candidate_id"]
    assert second != first and r.json()["portrait_url"].endswith(".jpg")
    listed = api.get("/api/projects/demo/characters").json()[0]
    assert listed["portrait_candidate_id"] == second
    slots = api.get("/api/slots", params={"owner_type": "character", "owner_id": ch["id"]}).json()
    assert slots[0]["kind"] == "portrait" and len(slots[0]["candidates"]) == 2


def test_portrait_from_local_path(api, tmp_path):
    ch = make_char(api)
    f = tmp_path / "xiao_yu.png"
    f.write_bytes(PNG)
    r = api.post(f"/api/characters/{ch['id']}/portrait", json={"path": str(f)})
    assert r.status_code == 200, r.text
    assert api.get(r.json()["portrait_url"]).content == PNG
    r = api.post(f"/api/characters/{ch['id']}/portrait", json={"path": str(tmp_path / "no.png")})
    assert r.status_code == 404
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    assert api.post(f"/api/characters/{ch['id']}/portrait", json={"path": str(txt)}).status_code == 400
    r = api.post(f"/api/characters/{ch['id']}/portrait", files={"file": ("a.exe", b"x", "x/y")})
    assert r.status_code == 400
    assert api.post("/api/characters/999/portrait", json={"path": str(f)}).status_code == 404


def test_delete_project_removes_character_slots(api):
    ch = make_char(api)
    api.post(f"/api/characters/{ch['id']}/portrait", files={"file": ("c.png", PNG, "image/png")})
    assert api.delete("/api/projects/demo").status_code == 204
    with api.app.state.services.db.session() as s:
        assert s.query(Slot).filter(Slot.owner_type == "character").count() == 0


def test_cli_character_portrait(api, monkeypatch, tmp_path):
    monkeypatch.setattr(studio, "_client", lambda: api)
    ch = make_char(api)
    f = tmp_path / "chen.png"
    f.write_bytes(PNG)
    r = CliRunner().invoke(studio.app, ["--json", "character", "portrait", "demo", "陈师傅",
                                        "--file", str(f)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["id"] == ch["id"]
    assert json.loads(r.stdout)["portrait_url"].endswith(".png")
    r = CliRunner().invoke(studio.app, ["character", "portrait", "demo", "nobody", "--file", str(f)])
    assert r.exit_code == 1 and "nobody" in r.output


# ---------------------------------------------------------------- 首帧 / 视频带参考图
def setup(tmp_path, db, *, image_engine="h3_image", video_engine="h3_video"):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    proj_id, ep_id = seed_episode(db, image_engine=image_engine, video_engine=video_engine)
    with db.session() as s:
        chen = Character(project_id=proj_id, name="陈师傅")
        yu = Character(project_id=proj_id, name="小雨")
        nobody = Character(project_id=proj_id, name="路人")
        s.add_all([chen, yu, nobody])
        s.flush()
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt=IMG, motion_prompt="he nods",
                  cast=[{"character_id": yu.id, "variation_id": None},
                        {"character_id": nobody.id, "variation_id": None},
                        {"character_id": chen.id, "variation_id": None}])
        s.add(sh)
        s.flush()
        ids = {"shot": sh.id, "chen": chen.id, "yu": yu.id, "project": proj_id}
    return FakeCtx(sv, job_id=7), runner, ids


def put_portrait(sv, char_id, name):
    from backend.domain.characters import set_portrait

    with sv.db.session() as s:
        c = set_portrait(s, sv.media, s.get(Character, char_id), PNG, name)
        return sv.media.abs(c.file_path), c.id


def stale(sv, shot_id, kind):
    from backend.pipeline.common import load_episode

    with sv.db.session() as s:
        sh = s.get(Shot, shot_id)
        _, proj = load_episode(s, sh.episode_id)
        d = shot_detail(s, sv, sh, proj, proj.style_bible)
        return next(x.stale for x in d.slots if x.kind == kind)


def test_keyframe_passes_cast_portraits_as_refs(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    yu_path, _ = put_portrait(sv, ids["yu"], "yu.png")
    chen_path, _ = put_portrait(sv, ids["chen"], "chen.png")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    call = runner.calls[0]
    # 按 cast 顺序；没有定妆照的角色跳过
    assert call.meta["refs"] == [str(yu_path), str(chen_path)]
    assert call.workflow["6"]["inputs"]["ref_images.ref_image_0"] == ["ref_0", 0]
    assert not stale(sv, ids["shot"], "first_frame")
    # 换了定妆照：首帧过期，重跑不再跳过
    put_portrait(sv, ids["chen"], "chen_v2.png")
    assert stale(sv, ids["shot"], "first_frame")
    assert run_keyframe(ctx, {"shot_id": ids["shot"]}).get("skipped") is None
    assert len(runner.calls) == 2
    assert run_keyframe(ctx, {"shot_id": ids["shot"]}) == {"skipped": True, "candidate_ids": []}


def test_keyframe_without_refs_keeps_old_hash(tmp_path, db):
    from backend.pipeline.prompting import keyframe_input_hash

    ctx, runner, ids = setup(tmp_path, db)
    with db.session() as s:
        sh = s.get(Shot, ids["shot"])
        proj = s.get(Project, ids["project"])
        assert (keyframe_input_hash(sh, proj, None, "h3_image")
                == keyframe_input_hash(sh, proj, None, "h3_image", ref_ids=[]))
        assert (keyframe_input_hash(sh, proj, None, "h3_image")
                != keyframe_input_hash(sh, proj, None, "h3_image", ref_ids=[3]))


def test_zimage_ignores_portraits(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db, image_engine="zimage")
    sv = ctx.services
    put_portrait(sv, ids["chen"], "chen.png")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    assert any("丢弃" in m for m in ctx.logs)
    # zimage 不吃参考图，换定妆照不算过期
    put_portrait(sv, ids["chen"], "chen_v2.png")
    assert not stale(sv, ids["shot"], "first_frame")


def test_video_passes_cast_portraits_as_refs(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    chen_path, _ = put_portrait(sv, ids["chen"], "chen.png")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    call = runner.calls[-1]
    assert call.meta["refs"] == [str(chen_path)]
    assert call.workflow["6"]["inputs"]["ref_images.ref_image_1"] == ["ref_0", 0]
    assert not stale(sv, ids["shot"], "video")
    # 新定妆照只给视频（首帧选定不变）：视频过期
    put_portrait(sv, ids["yu"], "yu.png")
    with db.session() as s:
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame").id
    assert stale(sv, ids["shot"], "video")
    assert run_video(ctx, {"shot_id": ids["shot"]}).get("skipped") is None
    with db.session() as s:
        assert selected_candidate(s, "shot", ids["shot"], "first_frame").id == ff


def stale_reason(sv, shot_id, kind):
    from backend.pipeline.common import load_episode

    with sv.db.session() as s:
        sh = s.get(Shot, shot_id)
        _, proj = load_episode(s, sh.episode_id)
        d = shot_detail(s, sv, sh, proj, proj.style_bible)
        return next(x.stale_reason for x in d.slots if x.kind == kind)


def test_stale_reason_portrait_changed(tmp_path, db):
    ctx, runner, ids = setup(tmp_path, db)
    sv = ctx.services
    _, chen_cid = put_portrait(sv, ids["chen"], "chen.png")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        assert ff.params["inputs"]["refs"] == [{"id": chen_cid, "label": "陈师傅 定妆照",
                                                  "kind": "portrait"}]
        assert ff.params["inputs"]["engine"] == "h3_image"
    assert stale_reason(sv, ids["shot"], "first_frame") is None
    put_portrait(sv, ids["chen"], "chen_v2.png")
    assert stale(sv, ids["shot"], "first_frame")
    assert stale_reason(sv, ids["shot"], "first_frame") == "陈师傅 定妆照换了"
    # 视频：记首帧和时长；换首帧后原因是「首帧换了」
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        ff_id = selected_candidate(s, "shot", ids["shot"], "first_frame").id
        assert v.params["inputs"]["first_frame"] == ff_id and "duration" in v.params["inputs"]
    assert stale_reason(sv, ids["shot"], "video") is None
    put_portrait(sv, ids["yu"], "yu.png")
    assert stale_reason(sv, ids["shot"], "video") == "加了 小雨 定妆照"


def test_character_last_failure(api):
    from backend.queue.errors import JobError
    from backend.tests.helpers import FakeRunner

    ch = make_char(api)
    sv, m = api.app.state.services, api.app.state.manager
    [jid] = api.post(f"/api/characters/{ch['id']}/portraits/draw", json={}).json()["job_ids"]
    got = api.get("/api/projects/demo/characters").json()[0]
    assert got["last_failure"] is None and [j["id"] for j in got["active_jobs"]] == [jid]
    sv.comfy = FakeRunner(fail=JobError("出图失败\n第二行"))
    m._execute(m._claim(gpu=True))
    got = api.get("/api/projects/demo/characters").json()[0]
    assert got["active_jobs"] == []
    lf = got["last_failure"]
    assert lf["id"] == jid and lf["type"] == "image.portrait" and lf["error"] == "出图失败"
    api.post(f"/api/characters/{ch['id']}/portraits/draw", json={})
    sv.comfy = FakeRunner()
    m._execute(m._claim(gpu=True))
    assert api.get("/api/projects/demo/characters").json()[0]["last_failure"] is None


def test_upload_after_failed_draw_clears_last_failure(api):
    """抽卡失败后用户自己上传了定妆照：失败提示不该再挂着。"""
    from backend.queue.errors import JobError

    ch = make_char(api)
    sv, m = api.app.state.services, api.app.state.manager
    api.post(f"/api/characters/{ch['id']}/portraits/draw", json={})
    sv.comfy = FakeRunner(fail=JobError("出图失败"))
    m._execute(m._claim(gpu=True))
    assert api.get("/api/projects/demo/characters").json()[0]["last_failure"] is not None
    r = api.post(f"/api/characters/{ch['id']}/portrait",
                 files={"file": ("chen.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["last_failure"] is None
    assert api.get("/api/projects/demo/characters").json()[0]["last_failure"] is None
