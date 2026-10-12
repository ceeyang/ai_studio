"""参考图直出（D3，设计文档 2026-10-12 §5）：镜头 video_mode = refs 时不出首帧，视频直接用场景图、构图参考、
定妆照 / 三视图、道具图出；打包和截断、输入哈希过期、和接上一镜互斥、引擎不支持时拒绝。"""
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.views import shot_detail
from backend.domain.characters import set_portrait
from backend.domain.locations import set_location_image
from backend.domain.models import Character, Job, Location, Project, SceneBlock, Shot, StyleBible
from backend.domain.refs import add_shot_ref
from backend.domain.shots import refs_mode, refs_mode_overridden
from backend.domain.slots import selected_candidate
from backend.engines.base import VideoRequest
from backend.pipeline.keyframes import engine_trace, run_keyframe
from backend.pipeline.prompting import stale_reason
from backend.pipeline.runs import Precheck, RunOptions, submit_chain
from backend.pipeline.videos import run_video
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.fakes.fake_ref_video import install
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

GARAGE = "A cramped auto repair garage with oil-stained concrete floor under a bare bulb."


def png(n: int = 1) -> bytes:
    from io import BytesIO

    from PIL import Image
    buf = BytesIO()
    Image.new("RGB", (8 + n, 8), (n * 20 % 255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def setup(tmp_path, db, *, video_mode="refs", max_refs=3, n=1, cont=()):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    eng = install(sv, max_refs=max_refs)
    proj_id, ep_id = seed_episode(db, image_engine="h3_image", video_engine=eng.name)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR", negative="cartoon")
        loc = Location(project_id=proj_id, name="修车行", time_of_day="夜晚", visual_prompt=GARAGE)
        s.add(loc)
        s.flush()
        blk = SceneBlock(episode_id=ep_id, order=0, location_id=loc.id, text="陈师傅修车")
        chen = Character(project_id=proj_id, name="陈师傅", visual_prompt="a man in his fifties")
        li = Character(project_id=proj_id, name="小李", visual_prompt="a young apprentice")
        s.add_all([blk, chen, li])
        s.flush()
        shots = []
        for i in range(n):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", scene_block_id=blk.id,
                      image_prompt="medium shot, a mechanic under a car", shot_size="中景",
                      motion_prompt="he slides out from under the car",
                      cast=[{"character_id": chen.id, "variation_id": None},
                            {"character_id": li.id, "variation_id": None}],
                      video_mode=video_mode, continue_from_prev=i in cont)
            s.add(sh)
            s.flush()
            shots.append(sh.id)
        ids = {"project": proj_id, "episode": ep_id, "loc": loc.id, "chen": chen.id, "li": li.id,
               "shots": shots, "shot": shots[0]}
    return FakeCtx(sv, job_id=41), runner, eng, ids


def _portrait(sv, char_id, n=3):
    with sv.db.session() as s:
        return set_portrait(s, sv.media, s.get(Character, char_id), png(n), "p.png").id


def _loc_image(sv, loc_id, n=1):
    with sv.db.session() as s:
        return set_location_image(s, sv.media, s.get(Location, loc_id), png(n), "loc.png").id


def _shot_ref(sv, shot_id, n=1):
    with sv.db.session() as s:
        return add_shot_ref(s, sv.media, s.get(Shot, shot_id), "demo", png(n), f"r{n}.jpg").id


def _detail(sv, shot_id):
    from backend.domain.models import Episode

    with sv.db.session() as s:
        shot = s.get(Shot, shot_id)
        proj = s.get(Project, s.get(Episode, shot.episode_id).project_id)
        return shot_detail(s, sv, shot, proj, proj.style_bible)


def _video_slot(d):
    return next(x for x in d.slots if x.kind == "video")


# ------------------------------------------------------------ 生成方式
def test_refs_mode_and_continue_from_prev_are_exclusive():
    assert refs_mode(Shot(video_mode="refs", continue_from_prev=False))
    assert not refs_mode(Shot(video_mode=None, continue_from_prev=False))
    assert not refs_mode(Shot(video_mode="first_frame", continue_from_prev=False))
    # 接上一镜优先：按首帧模式
    assert not refs_mode(Shot(video_mode="refs", continue_from_prev=True))
    assert refs_mode_overridden(Shot(video_mode="refs", continue_from_prev=True))


# ------------------------------------------------------------ 首帧阶段
def test_keyframe_skips_refs_mode_shot(tmp_path, db):
    ctx, runner, _, ids = setup(tmp_path, db)
    res = run_keyframe(ctx, {"shot_id": ids["shot"]})
    assert res["skipped"] and res["candidate_ids"] == []
    assert runner.calls == []
    assert any("这一镜参考图直出，不出首帧" in m for m in ctx.logs)


# ------------------------------------------------------------ 视频阶段
def test_video_without_first_frame_uses_packed_refs(tmp_path, db):
    ctx, runner, eng, ids = setup(tmp_path, db, max_refs=9)
    sv = ctx.services
    p1, p2 = _portrait(sv, ids["chen"], 3), _portrait(sv, ids["li"], 4)
    lid = _loc_image(sv, ids["loc"])
    r1 = _shot_ref(sv, ids["shot"], 1)
    res = run_video(ctx, {"shot_id": ids["shot"]})
    req = eng.requests[-1]
    assert req.first_frame is None
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        assert v.id == res["candidate_ids"][0]
        assert v.params["video_mode"] == "refs" and v.params["first_frame_candidate_id"] is None
        # 出首帧的同一套打包：场景图 → 构图参考 → 定妆照
        assert [r["id"] for r in v.params["video_refs"]] == [lid, r1, p1, p2]
        assert [r["kind"] for r in v.params["video_refs"]] == ["location", "shot_ref", "portrait",
                                                              "portrait"]
        assert v.params["inputs"]["mode"] == "refs" and "first_frame" not in v.params["inputs"]
        assert v.params["identity"] == "refs"
        assert sorted(v.params["locked_chars"]) == sorted([ids["chen"], ids["li"]])
        paths = [str(sv.media.abs(s.get(type(v), c).file_path)) for c in (lid, r1, p1, p2)]
    assert [str(p) for p in req.refs] == paths
    assert any("参考图直出" in m for m in ctx.logs)
    # 输入没变：再跑跳过
    assert run_video(ctx, {"shot_id": ids["shot"]})["skipped"]


def test_refs_mode_trims_with_split_refs(tmp_path, db):
    """上限 2 张：先保住每个角色一张定妆照，场景图、构图参考让位。"""
    ctx, _, eng, ids = setup(tmp_path, db, max_refs=2)
    sv = ctx.services
    p1, p2 = _portrait(sv, ids["chen"], 3), _portrait(sv, ids["li"], 4)
    _loc_image(sv, ids["loc"])
    _shot_ref(sv, ids["shot"], 1)
    run_video(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        assert [r["id"] for r in v.params["video_refs"]] == [p1, p2]
    assert len(eng.requests[-1].refs) == 2
    assert any("已丢弃：场景图·修车行" in m for m in ctx.logs)
    assert any("已丢弃：构图参考 1" in m for m in ctx.logs)
    d = _detail(sv, ids["shot"])
    assert [(r.label, r.used) for r in d.video_refs] == [
        ("场景图·修车行", False), ("构图参考 1", False), ("陈师傅 定妆照", True), ("小李 定妆照", True)]


def test_refs_mode_video_goes_stale_when_portrait_changes(tmp_path, db):
    ctx, _, _, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"], 3)
    _loc_image(sv, ids["loc"])
    run_video(ctx, {"shot_id": ids["shot"]})
    assert not _video_slot(_detail(sv, ids["shot"])).stale
    _portrait(sv, ids["chen"], 5)   # 换了一张定妆照
    slot = _video_slot(_detail(sv, ids["shot"]))
    assert slot.stale and "陈师傅 定妆照换了" in slot.stale_reason
    # 批量重跑不再跳过
    res = run_video(ctx, {"shot_id": ids["shot"]})
    assert not res.get("skipped") and res["candidate_ids"]
    assert not _video_slot(_detail(sv, ids["shot"])).stale


def test_refs_mode_video_goes_stale_when_location_image_changes(tmp_path, db):
    ctx, _, _, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"], 3)
    _loc_image(sv, ids["loc"], 1)
    run_video(ctx, {"shot_id": ids["shot"]})
    _loc_image(sv, ids["loc"], 2)
    slot = _video_slot(_detail(sv, ids["shot"]))
    assert slot.stale and "场景图·修车行" in slot.stale_reason


def test_switching_mode_makes_video_stale(tmp_path, db):
    ctx, _, _, ids = setup(tmp_path, db)
    sv = ctx.services
    _portrait(sv, ids["chen"], 3)
    run_video(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        s.get(Shot, ids["shot"]).video_mode = "first_frame"
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    slot = _video_slot(_detail(sv, ids["shot"]))
    assert slot.stale and slot.stale_reason == "改回了首帧模式"
    assert stale_reason({"refs": [], "prompt": "a"}, {"refs": [], "prompt": "a", "mode": "refs"}) \
        == "改成了参考图直出"


def test_refs_mode_qc_skips_first_frame_ssim_keeps_ghost_check(tmp_path, db):
    class QcFFmpeg:
        ffmpeg = "ffmpeg"

        def __init__(self):
            self.ssim_calls, self.ghost_refs = 0, []

        def scene_cuts(self, path):
            return []

        def first_frame_ssim(self, path, first):
            self.ssim_calls += 1
            return 0.1

        def video_duration(self, path):
            return 4.0

        def frame_ssim(self, path, t, ref):
            self.ghost_refs.append(Path(ref).name)
            return 0.0

    ctx, _, _, ids = setup(tmp_path, db)
    sv = ctx.services
    qc = QcFFmpeg()
    sv.ffmpeg = qc
    _portrait(sv, ids["chen"], 3)
    _loc_image(sv, ids["loc"])
    run_video(ctx, {"shot_id": ids["shot"]})
    assert qc.ssim_calls == 0
    with db.session() as s:
        from backend.domain.characters import portrait_of
        portrait = Path(portrait_of(s, ids["chen"]).file_path).name
    # 叠入参考图只比带脸的图（场景图本来就该像画面，不比）
    assert qc.ghost_refs and set(qc.ghost_refs) == {portrait}
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        assert "start_mismatch" not in (v.params.get("qc") or {})


def test_continue_from_prev_wins_over_refs_mode(tmp_path, db):
    ctx, runner, eng, ids = setup(tmp_path, db, n=2, cont=(1,))
    sv = ctx.services
    second = ids["shots"][1]
    # 首帧阶段走接上一镜（截尾帧），不按参考图直出跳过
    with pytest.raises(JobError, match="上一镜"):
        run_keyframe(ctx, {"shot_id": second})
    assert not any("参考图直出，不出首帧" in m for m in ctx.logs)
    # 视频阶段要首帧，并说明参考图直出没生效
    with pytest.raises(JobError, match="还没有选定首帧"):
        run_video(ctx, {"shot_id": second})
    assert any("参考图直出不生效" in m for m in ctx.logs)


def test_engine_without_ref_mode_refused_in_job(tmp_path, db):
    ctx, _, _, ids = setup(tmp_path, db)
    with db.session() as s:
        s.get(Shot, ids["shot"]).video_engine = "h3_video"
    with pytest.raises(JobError, match="h3_video 不支持参考图直出"):
        run_video(ctx, {"shot_id": ids["shot"]})


def test_engine_build_without_first_frame_raises_for_non_ref_engines(tmp_path, db):
    sv = make_services(tmp_path, db, comfy=FakeRunner())
    # 打开了 ref_mode 的：Seedance API（多模态参考，见 SeedanceEngine docstring），Gemini / Muse 网页
    #（附图当人物 / 场景参考，固定时长引擎设计 §3.5）
    assert {n for n in sv.engines.names("video") if sv.engines.video(n).caps.ref_mode} == \
        {"seedance_video", "gemini_video", "muse_video"}
    for name in sv.engines.names("video"):
        eng = sv.engines.video(name)
        if eng.caps.ref_mode:
            continue
        with pytest.raises(ValueError, match="不支持参考图直出"):
            eng.build(VideoRequest(prompt="x", first_frame=None, aspect="9:16"))


def test_engine_trace_lists_refs_without_first_frame(tmp_path, db):
    ctx, _, eng, ids = setup(tmp_path, db)
    req = VideoRequest(prompt="x", aspect="9:16", refs=[Path("/tmp/a.png"), Path("/tmp/b.png")])
    call = eng.build(req)
    _, data = engine_trace(call, req, {"/tmp/a.png": "场景图·修车行", "/tmp/b.png": "陈师傅 定妆照"},
                           {"/tmp/a.png": "场景图", "/tmp/b.png": "定妆照"}, "视频 S01")
    assert [(r["label"], r["role"]) for r in data["refs"]] == [("场景图·修车行", "场景图"),
                                                               ("陈师傅 定妆照", "定妆照")]


# ------------------------------------------------------------ 提交
@pytest.fixture
def manager(db):
    return JobManager(db, HANDLERS, poll=0.02)   # 不启动 worker，只看提交了什么


def _jobs(db):
    with db.session() as s:
        return [(j.id, j.type, j.payload.get("shot_id"), list(j.depends_on))
                for j in s.scalars(select(Job).order_by(Job.id))]


def test_chain_skips_keyframe_and_video_does_not_wait(tmp_path, db, manager):
    ctx, _, _, ids = setup(tmp_path, db, n=2)
    a, b = ids["shots"]
    with db.session() as s:
        s.get(Shot, b).video_mode = None   # 第二镜照常首帧模式
        s.get(Shot, b).video_engine = "fake_refs_video"
    submit_chain(manager, db, ctx.services.engines, ids["episode"], ["keyframes", "videos"],
                 RunOptions())
    rows = _jobs(db)
    by = {(r[1], r[2]): r for r in rows}
    assert ("image.keyframe", a) not in by
    assert by[("video.shot", a)][3] == []
    assert by[("video.shot", b)][3] == [by[("image.keyframe", b)][0]]


def test_precheck_videos_only_does_not_need_first_frame(tmp_path, db, manager):
    ctx, _, _, ids = setup(tmp_path, db)
    out = submit_chain(manager, db, ctx.services.engines, ids["episode"], ["videos"], RunOptions(),
                       precheck=Precheck())
    assert len(out) == 1
    # 只跑首帧：没有可提交的，不报错
    assert submit_chain(manager, db, ctx.services.engines, ids["episode"], ["keyframes"],
                        RunOptions(), precheck=Precheck()) == []


def test_chain_refuses_engine_without_ref_mode(tmp_path, db, manager):
    ctx, _, _, ids = setup(tmp_path, db)
    with db.session() as s:
        s.get(Shot, ids["shot"]).video_engine = "h3_video"
    with pytest.raises(JobError, match="S01：h3_video 不支持参考图直出"):
        submit_chain(manager, db, ctx.services.engines, ids["episode"], ["keyframes", "videos"],
                     RunOptions())
    assert _jobs(db) == []   # 一个任务都没提交


# ------------------------------------------------------------ 接口
def test_patch_video_mode(api):
    client = api
    r = client.post("/api/projects", json={"slug": "vm", "title": "t", "image_engine": "zimage"})
    assert r.status_code == 201, r.text
    ep = client.post("/api/projects/vm/episodes", json={"source_text": "x"}).json()
    sh = client.post(f"/api/episodes/{ep['id']}/shots", json={"image_prompt": "a"}).json()
    assert sh["video_mode"] is None and sh["video_refs"] == []
    r = client.patch(f"/api/shots/{sh['id']}", json={"video_mode": "refs"})
    assert r.status_code == 200 and r.json()["video_mode"] == "refs"
    assert client.patch(f"/api/shots/{sh['id']}", json={"video_mode": "bad"}).status_code == 422
    r = client.patch(f"/api/shots/{sh['id']}", json={"video_mode": None})
    assert r.json()["video_mode"] is None
