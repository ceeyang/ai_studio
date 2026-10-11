import threading
import time

import pytest
from sqlalchemy import select

import backend.pipeline  # noqa: F401  注册全部处理器
from backend.domain.models import Episode, Export, Shot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.pipeline.export import run_export
from backend.pipeline.runs import RunOptions, submit_chain
from backend.pipeline.script import run_script
from backend.queue.errors import JobCanceled, JobError
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, FakeFFmpeg, make_gateway, make_services, seed_episode


def seed_shots(db, n=3, **kw):
    _, ep_id = seed_episode(db, **kw)
    ids = []
    with db.session() as s:
        for i in range(n):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}",
                      transition_out="dissolve_0.5" if i == 0 else "hard_cut")
            s.add(sh)
            s.flush()
            ids.append(sh.id)
    return ep_id, ids


def add_video(sv, shot_id, name):
    p = sv.media.new_media_path("demo", "video", "mp4")
    p.write_bytes(b"v")
    with sv.db.session() as s:
        add_candidate(s, get_or_create_slot(s, "shot", shot_id, "video"), file_path=sv.media.rel(p),
                      engine="h3_video", params={}, input_hash=name, job_id=None)
    return p


def test_export_assembles_selected_videos_in_order(tmp_path, db):
    ff = FakeFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    ep_id, ids = seed_shots(db)
    paths = [add_video(sv, sid, f"h{i}") for i, sid in enumerate(ids)]
    res = run_export(FakeCtx(sv, job_id=5), {"episode_id": ep_id})
    call = ff.calls[0]
    assert call["clips"] == paths
    assert (call["width"], call["height"], call["fps"]) == (1080, 1920, 24)
    assert call["transitions"] == ["dissolve_0.5", "hard_cut"]
    assert res["file"].startswith("demo/exports/ep01_9x16_") and res["file"].endswith(".mp4")
    with db.session() as s:
        assert s.get(Export, res["export_id"]).variant == "9x16"
        assert s.get(Episode, ep_id).status == "exported"


def test_export_warns_about_flagged_hard_cuts(tmp_path, db):
    """选定的视频出片自检疑似跳切：导出照常进行，日志里列出来。"""
    from backend.domain.slots import selected_candidate

    sv = make_services(tmp_path, db, ffmpeg=FakeFFmpeg())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    with db.session() as s:
        c = selected_candidate(s, "shot", ids[1], "video")
        c.params = {"qc": {"hard_cuts": [2.04]}}
    ctx = FakeCtx(sv, job_id=5)
    run_export(ctx, {"episode_id": ep_id})
    assert any("疑似跳切" in m and "S02 2.0s" in m for m in ctx.logs)


def test_export_warns_when_clip_is_shorter_than_dialogue(tmp_path, db):
    """视频实际时长（假 ffmpeg 都是 5 秒）比最后一句台词短：导出照常，日志提示会被截掉。"""
    sv = make_services(tmp_path, db, ffmpeg=FakeFFmpeg())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    with db.session() as s:
        s.get(Shot, ids[2]).dialogue = [{"speaker": "a", "zh": "最后一句", "start": 4.0, "end": 5.3}]
    ctx = FakeCtx(sv, job_id=5)
    run_export(ctx, {"episode_id": ep_id})
    assert any("S03（视频 5.00s，台词到 5.30s）" in m for m in ctx.logs)


def test_export_reports_missing_videos(tmp_path, db):
    ff = FakeFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    ep_id, ids = seed_shots(db)
    add_video(sv, ids[0], "a")
    with pytest.raises(JobError, match="S02, S03"):
        run_export(FakeCtx(sv), {"episode_id": ep_id})
    run_export(FakeCtx(sv), {"episode_id": ep_id, "allow_missing": True})
    assert len(ff.calls[0]["clips"]) == 1 and ff.calls[0]["transitions"] == []


@pytest.fixture
def manager(db):
    return JobManager(db, HANDLERS, poll=0.02)  # 不启动 worker，只看提交了什么


def jobs(db):
    from backend.domain.models import Job
    with db.session() as s:
        return [(j.id, j.type, j.payload, j.depends_on, j.model_family, j.gpu)
                for j in s.scalars(select(Job).order_by(Job.id))]


def test_chain_keyframes_videos_export(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, ids = seed_shots(db, n=2)
    out = submit_chain(manager, db, sv.engines, ep_id, ["keyframes", "videos", "export"],
                       RunOptions(candidates=2))
    rows = jobs(db)
    assert [r[1] for r in rows] == ["image.keyframe"] * 4 + ["video.shot"] * 2 + ["export.episode"]
    kf = {sid: [r[0] for r in rows if r[1] == "image.keyframe" and r[2]["shot_id"] == sid] for sid in ids}
    vids = [r for r in rows if r[1] == "video.shot"]
    assert vids[0][3] == kf[ids[0]] and vids[1][3] == kf[ids[1]]
    assert rows[-1][3] == [v[0] for v in vids]
    assert rows[0][4] == "zimage" and vids[0][4] == "h3" and rows[0][5] is True
    assert rows[0][2] == {"shot_id": ids[0], "force": False, "candidates": 2}
    assert vids[0][2] == {"shot_id": ids[0], "force": False}
    assert out == [r[0] for r in rows]


def test_chain_validates_all_shot_engines_before_submitting(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, ids = seed_shots(db, n=3)
    with db.session() as s:
        s.get(Shot, ids[1]).image_engine = "nope"
    with pytest.raises(JobError):
        submit_chain(manager, db, sv.engines, ep_id, ["keyframes", "videos"], RunOptions())
    assert jobs(db) == []


def test_chain_shot_filter(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    ep_id, ids = seed_shots(db)
    submit_chain(manager, db, sv.engines, ep_id, ["keyframes"], RunOptions(shots=["s02", str(ids[2])]))
    assert [r[2]["shot_id"] for r in jobs(db)] == [ids[1], ids[2]]
    submit_chain(manager, db, sv.engines, ep_id, ["keyframes", "videos"],
                 RunOptions(shots=["S01"], force=True))
    assert [r[2] for r in jobs(db)[2:]] == [{"shot_id": ids[0], "force": True, "candidates": 1},
                                           {"shot_id": ids[0], "force": True}]
    with pytest.raises(JobError, match="S09"):
        submit_chain(manager, db, sv.engines, ep_id, ["keyframes"], RunOptions(shots=["S09"]))


def test_chain_validation(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    _, ep_id = seed_episode(db)
    with pytest.raises(JobError, match="分镜"):
        submit_chain(manager, db, sv.engines, ep_id, ["keyframes"], RunOptions())
    with pytest.raises(JobError, match="未知阶段"):
        submit_chain(manager, db, sv.engines, ep_id, ["render"], RunOptions())
    with pytest.raises(JobError, match="最前面"):
        submit_chain(manager, db, sv.engines, ep_id, ["keyframes", "storyboard"], RunOptions())


def test_chain_starting_with_script_defers_the_rest(tmp_path, db, manager):
    sv = make_services(tmp_path, db)
    _, ep_id = seed_episode(db)
    [jid] = submit_chain(manager, db, sv.engines, ep_id, list(("script", "storyboard", "keyframes")),
                         RunOptions(force=True, candidates=2))
    (_, jtype, payload, _, _, gpu), = jobs(db)
    assert jtype == "script" and gpu is False
    assert payload["then"] == ["storyboard", "keyframes"]
    assert payload["options"]["candidates"] == 2 and payload["force"] is True


def test_script_handler_continues_chain(tmp_path, db, manager):
    from backend.tests.test_script import STRUCTURE, VISUAL
    gw, _ = make_gateway([STRUCTURE, VISUAL])
    sv = make_services(tmp_path, db, llm=gw)
    manager.services = sv
    _, ep_id = seed_episode(db)
    run_script(FakeCtx(sv, manager=manager),
               {"episode_id": ep_id, "then": ["storyboard", "keyframes"],
                "options": {"candidates": 1}})
    (_, jtype, payload, _, _, _), = jobs(db)
    assert jtype == "storyboard" and payload["then"] == ["keyframes"]


# ---------------------------------------------------------------- 取消导出
def test_export_cancel_between_steps_stops_and_records_nothing(tmp_path, db):
    """取消正在跑的导出：合成完这一步后看到取消，不再往下做，也不登记导出。"""
    holder = {}

    class CancelAfterAssemble(FakeFFmpeg):
        def assemble(self, *a, **kw):
            res = super().assemble(*a, **kw)
            holder["ctx"].cancel_event.set()
            return res

    sv = make_services(tmp_path, db, ffmpeg=CancelAfterAssemble())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    ctx = holder["ctx"] = FakeCtx(sv, job_id=5)
    with pytest.raises(JobCanceled):
        run_export(ctx, {"episode_id": ep_id})
    with db.session() as s:
        assert s.scalars(select(Export)).all() == []
        assert s.get(Episode, ep_id).status != "exported"


def test_export_uses_ffmpeg_bound_to_job_cancel(tmp_path, db):
    """真 ffmpeg 走 job_services：拿到的是绑定了本任务取消信号的副本，共享实例不受影响。"""
    from backend.pipeline.common import job_services

    sv = make_services(tmp_path, db)
    ctx = FakeCtx(sv)
    bound = job_services(ctx)
    assert bound.ffmpeg.cancel is ctx.cancel_event
    assert sv.ffmpeg.cancel is None and bound.db is sv.db


def test_export_job_cancel_through_manager_ends_canceled(tmp_path, db):
    """经由队列：运行中的导出被取消后状态是 canceled（之前接口回 ok，任务却照样跑完记成 done）。"""
    started, release = threading.Event(), threading.Event()

    class Slow(FakeFFmpeg):
        def assemble(self, *a, **kw):
            started.set()
            release.wait(5)
            return super().assemble(*a, **kw)

    sv = make_services(tmp_path, db, ffmpeg=Slow())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    m = JobManager(db, HANDLERS, poll=0.02)
    m.services = sv
    m.start()
    try:
        jid = m.submit("export.episode", {"episode_id": ep_id}, owner_type="episode",
                       owner_id=ep_id)
        assert started.wait(5)
        assert m.cancel(jid) is True
        release.set()
        end = time.monotonic() + 5
        while time.monotonic() < end and m.get(jid)["status"] in ("queued", "running"):
            time.sleep(0.02)
        assert m.get(jid)["status"] == "canceled"
        with db.session() as s:
            assert s.scalars(select(Export)).all() == []
    finally:
        release.set()
        m.stop()


def test_export_cancel_removes_partial_output_and_work_dir(tmp_path, db):
    """取消时不留半成品：exports/ 里不能有像正式导出那样命名的坏 mp4，runs/<任务>/work 也要删掉。"""
    holder = {}

    class CancelAfterAssemble(FakeFFmpeg):
        def assemble(self, clips, out, **kw):
            res = super().assemble(clips, out, **kw)
            (kw["work_dir"]).mkdir(parents=True, exist_ok=True)
            (kw["work_dir"] / "seg0.mp4").write_bytes(b"x")
            holder["ctx"].cancel_event.set()
            return res

    sv = make_services(tmp_path, db, ffmpeg=CancelAfterAssemble())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    ctx = holder["ctx"] = FakeCtx(sv, job_id=7)
    with pytest.raises(JobCanceled):
        run_export(ctx, {"episode_id": ep_id})
    exports = sv.media.root / "demo" / "exports"
    assert not exports.exists() or list(exports.iterdir()) == []
    assert not (sv.media.run_dir("demo", 7) / "work").exists()


def test_export_failure_also_removes_partial_output(tmp_path, db):
    class Boom(FakeFFmpeg):
        def assemble(self, clips, out, **kw):
            super().assemble(clips, out, **kw)
            raise JobError("ffmpeg 合成失败")

    sv = make_services(tmp_path, db, ffmpeg=Boom())
    ep_id, ids = seed_shots(db)
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    with pytest.raises(JobError):
        run_export(FakeCtx(sv, job_id=8), {"episode_id": ep_id})
    exports = sv.media.root / "demo" / "exports"
    assert not exports.exists() or list(exports.iterdir()) == []


def test_export_continue_from_prev_keeps_tail_and_hard_cuts(tmp_path, db):
    """S02 接上一镜（首帧取 S01 视频的最后一帧）：S01 不能按分镜裁尾，接缝强制硬切，
    切点正好落在抽出来的那一帧上。没接上一镜的 S03 照常裁尾。"""
    ff = FakeFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    ep_id, ids = seed_shots(db)
    with db.session() as s:
        for sid in ids:
            s.get(Shot, sid).duration = 3.0           # 视频 5 秒，分镜 3 秒：本来都要裁尾
        s.get(Shot, ids[1]).continue_from_prev = True  # S01 → S02 原本是 dissolve_0.5
    for i, sid in enumerate(ids):
        add_video(sv, sid, f"h{i}")
    ctx = FakeCtx(sv, job_id=5)
    run_export(ctx, {"episode_id": ep_id})
    call = ff.calls[0]
    assert call["transitions"] == ["hard_cut", "hard_cut"]
    lim = call["max_seconds"]
    assert lim[0] is None and lim[1] is not None and lim[2] is not None
    assert any("接上一镜" in m and "S01" in m and "S02" in m for m in ctx.logs)


def test_export_continue_from_prev_with_missing_prev_changes_nothing(tmp_path, db):
    """上一镜没有选定视频（allow_missing 跳过）时，接上一镜那一镜前面接的是别的镜头，不改转场。"""
    ff = FakeFFmpeg()
    sv = make_services(tmp_path, db, ffmpeg=ff)
    ep_id, ids = seed_shots(db)
    with db.session() as s:
        s.get(Shot, ids[2]).continue_from_prev = True
        s.get(Shot, ids[0]).transition_out = "dissolve_0.5"
    add_video(sv, ids[0], "a")
    add_video(sv, ids[2], "c")
    run_export(FakeCtx(sv), {"episode_id": ep_id, "allow_missing": True})
    assert ff.calls[0]["transitions"] == ["dissolve_0.5"]
