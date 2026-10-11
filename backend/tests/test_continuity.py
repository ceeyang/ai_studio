"""接上一镜（D）：首帧取上一镜选定视频的尾帧；过期跟上一镜视频走；多镜一起出视频时的任务顺序。"""
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.views import shot_detail
from backend.domain.models import Job, Project, Shot
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate
from backend.pipeline.continuity import continue_hash, run_continue_frame
from backend.pipeline.keyframes import run_keyframe
from backend.pipeline.runs import Precheck, RunOptions, submit_chain
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS
from backend.queue.manager import JobManager
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode


class TailFFmpeg:
    """只实现截尾帧：把视频文件内容加个前缀写成 png，好认出是从哪条视频截的。"""
    ffmpeg = "ffmpeg"

    def __init__(self):
        self.calls = []

    def last_frame(self, video, out_png):
        self.calls.append((Path(video), Path(out_png)))
        Path(out_png).parent.mkdir(parents=True, exist_ok=True)
        Path(out_png).write_bytes(b"tail:" + Path(video).read_bytes())
        return Path(out_png)


def setup(tmp_path, db, n=3, cont=(1,)):
    runner = FakeRunner()
    ff = TailFFmpeg()
    sv = make_services(tmp_path, db, comfy=runner, ffmpeg=ff)
    proj_id, ep_id = seed_episode(db)
    ids = []
    with db.session() as s:
        for i in range(n):
            sh = Shot(episode_id=ep_id, order=i, code=f"S{i + 1:02d}", image_prompt=f"shot {i}",
                      continue_from_prev=i in cont)
            s.add(sh)
            s.flush()
            ids.append(sh.id)
    return FakeCtx(sv, job_id=31), runner, ff, ep_id, ids


def give_video(sv, shot_id, tag) -> int:
    dst = sv.media.new_media_path("demo", "video", ".mp4")
    dst.write_bytes(tag.encode())
    with sv.db.session() as s:
        c = add_candidate(s, get_or_create_slot(s, "shot", shot_id, "video"),
                          file_path=sv.media.rel(dst), engine="h3_video", params={},
                          input_hash=f"v-{tag}", job_id=None, force_select=True)
        return c.id


def detail(sv, shot_id):
    with sv.db.session() as s:
        shot = s.get(Shot, shot_id)
        proj = s.scalar(select(Project))
        return shot_detail(s, sv, shot, proj, proj.style_bible)


def ff_slot(d):
    return next((x for x in d.slots if x.kind == "first_frame"), None)


def test_continue_needs_prev_video(tmp_path, db):
    ctx, _, ff, _, ids = setup(tmp_path, db)
    with pytest.raises(JobError, match="上一镜 S01 还没有选定视频"):
        run_continue_frame(ctx, {"shot_id": ids[1]})
    assert ff.calls == []
    d = detail(ctx.services, ids[1])
    assert d.continue_prev is not None and d.continue_prev.code == "S01" and not d.continue_prev.has_video
    assert detail(ctx.services, ids[0]).continue_prev is None


def test_first_shot_cannot_continue(tmp_path, db):
    ctx, _, _, _, ids = setup(tmp_path, db, cont=(0,))
    with pytest.raises(JobError, match="第一镜"):
        run_continue_frame(ctx, {"shot_id": ids[0]})


def test_continue_extracts_last_frame_and_tracks_prev_video(tmp_path, db):
    ctx, runner, ff, _, ids = setup(tmp_path, db)
    sv = ctx.services
    v1 = give_video(sv, ids[0], "A")
    res = run_continue_frame(ctx, {"shot_id": ids[1]})
    assert runner.calls == []                                       # 不出图
    with db.session() as s:
        c = selected_candidate(s, "shot", ids[1], "first_frame")
        assert c.id == res["candidate_ids"][0] and c.engine == "continue"
        assert c.input_hash == continue_hash(v1)
        assert c.params["prev_video_candidate_id"] == v1 and c.params["inputs"]["prev_shot"] == "S01"
        assert sv.media.abs(c.file_path).read_bytes() == b"tail:A"
    d = detail(sv, ids[1])
    assert d.continue_prev.has_video and not ff_slot(d).stale
    assert d.first_frame_refs_unused == []
    # 上一镜视频没换：跳过
    assert run_continue_frame(ctx, {"shot_id": ids[1]})["skipped"] is True
    # 上一镜换了视频：首帧过期，重新截
    v2 = give_video(sv, ids[0], "B")
    d = detail(sv, ids[1])
    assert ff_slot(d).stale and ff_slot(d).stale_reason == "上一镜 S01 的视频换了"
    ctx.job_id += 1
    run_continue_frame(ctx, {"shot_id": ids[1]})
    with db.session() as s:
        c = selected_candidate(s, "shot", ids[1], "first_frame")
        assert c.input_hash == continue_hash(v2) and sv.media.abs(c.file_path).read_bytes() == b"tail:B"
    assert not ff_slot(detail(sv, ids[1])).stale
    # 选回第一条视频：又过期
    with db.session() as s:
        select_candidate(s, v1)
    assert ff_slot(detail(sv, ids[1])).stale


def test_keyframe_job_on_continue_shot_delegates(tmp_path, db):
    ctx, runner, ff, _, ids = setup(tmp_path, db)
    give_video(ctx.services, ids[0], "A")
    run_keyframe(ctx, {"shot_id": ids[1]})
    assert runner.calls == [] and len(ff.calls) == 1


def test_toggle_on_makes_generated_first_frame_stale(tmp_path, db):
    ctx, _, _, _, ids = setup(tmp_path, db, cont=())
    run_keyframe(ctx, {"shot_id": ids[1]})
    assert not ff_slot(detail(ctx.services, ids[1])).stale
    with db.session() as s:
        s.get(Shot, ids[1]).continue_from_prev = True
    d = detail(ctx.services, ids[1])
    assert ff_slot(d).stale and "S01 还没有选定视频" in ff_slot(d).stale_reason
    give_video(ctx.services, ids[0], "A")
    assert ff_slot(detail(ctx.services, ids[1])).stale_reason == "改成了接上一镜"


# ------------------------------------------------------------ 提交顺序
@pytest.fixture
def manager(db):
    return JobManager(db, HANDLERS, poll=0.02)   # 不启动 worker，只看提交了什么


def jobs(db):
    with db.session() as s:
        return [(j.id, j.type, j.payload.get("shot_id"), list(j.depends_on), j.gpu)
                for j in s.scalars(select(Job).order_by(Job.id))]


def test_chain_videos_wait_for_prev_video(tmp_path, db, manager):
    ctx, _, _, ep_id, ids = setup(tmp_path, db, cont=(1, 2))
    out = submit_chain(manager, db, ctx.services.engines, ep_id, ["keyframes", "videos"],
                       RunOptions())
    rows = jobs(db)
    by = {(r[1], r[2]): r for r in rows}
    kf1 = by[("image.keyframe", ids[0])]
    v1, v2, v3 = (by[("video.shot", i)] for i in ids)
    c2, c3 = by[("image.continue_frame", ids[1])], by[("image.continue_frame", ids[2])]
    assert ("image.keyframe", ids[1]) not in by and ("image.keyframe", ids[2]) not in by
    assert v1[3] == [kf1[0]]
    assert c2[3] == [v1[0]] and v2[3] == [c2[0]]
    assert c3[3] == [v2[0]] and v3[3] == [c3[0]]
    assert c2[4] is False                                         # 截尾帧是 CPU 任务
    assert out == [r[0] for r in rows]


def test_chain_keyframes_only_submits_continue_job(tmp_path, db, manager):
    ctx, _, _, ep_id, ids = setup(tmp_path, db)
    submit_chain(manager, db, ctx.services.engines, ep_id, ["keyframes"], RunOptions(candidates=2))
    rows = jobs(db)
    assert [(r[1], r[2]) for r in rows] == [("image.keyframe", ids[0]), ("image.keyframe", ids[0]),
                                            ("image.continue_frame", ids[1]),
                                            ("image.keyframe", ids[2]), ("image.keyframe", ids[2])]


def test_chain_videos_precheck(tmp_path, db, manager):
    ctx, _, _, ep_id, ids = setup(tmp_path, db)
    sv = ctx.services
    pre = Precheck()
    with pytest.raises(JobError, match="S02（接上一镜，上一镜 S01 还没有视频）"):
        submit_chain(manager, db, sv.engines, ep_id, ["videos"], RunOptions(shots=["S02"]),
                     precheck=pre)
    give_video(sv, ids[0], "A")
    submit_chain(manager, db, sv.engines, ep_id, ["videos"], RunOptions(shots=["S02"]), precheck=pre)
    assert [(r[1], r[2], r[3]) for r in jobs(db)][:1] == [("image.continue_frame", ids[1], [])]
    # 上一镜在同一批里出视频：不算缺
    with db.session() as s:
        from backend.domain.slots import get_or_create_slot as slot_of
        slot_of(s, "shot", ids[0], "video").selected_candidate_id = None
        add_candidate(s, slot_of(s, "shot", ids[0], "first_frame"), file_path="demo/x.png",
                      engine="zimage", params={}, input_hash="h", job_id=None)
    submit_chain(manager, db, sv.engines, ep_id, ["videos"], RunOptions(shots=["S02", "S01"]),
                 precheck=pre)
    tail = [(r[1], r[2]) for r in jobs(db)][-3:]
    assert tail == [("video.shot", ids[0]), ("image.continue_frame", ids[1]), ("video.shot", ids[1])]


def test_real_ffmpeg_last_frame(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("没有 ffmpeg")
    from backend.engines.ffmpeg import FFmpeg
    import subprocess
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-pix_fmt",
                    "yuv420p", str(clip)], check=True, capture_output=True)
    out = FFmpeg().last_frame(clip, tmp_path / "t.png")
    assert out.read_bytes()[:4] == b"\x89PNG"


def test_continue_waits_for_prev_video_job_already_queued(tmp_path, db, manager):
    """上一镜的视频已经在队列里（之前单独提交的）：这一镜截尾帧排在它后面，提交前校验也不拦。"""
    ctx, _, _, ep_id, ids = setup(tmp_path, db)
    sv = ctx.services
    with db.session() as s:
        add_candidate(s, get_or_create_slot(s, "shot", ids[0], "first_frame"), file_path="demo/x.png",
                      engine="zimage", params={}, input_hash="h", job_id=None)
    first = submit_chain(manager, db, sv.engines, ep_id, ["videos"], RunOptions(shots=["S01"]),
                         precheck=Precheck())
    submit_chain(manager, db, sv.engines, ep_id, ["videos"], RunOptions(shots=["S02"]),
                 precheck=Precheck())
    rows = [(r[1], r[2], r[3]) for r in jobs(db)]
    assert rows[1:] == [("image.continue_frame", ids[1], first),
                        ("video.shot", ids[1], [jobs(db)[1][0]])]
    submit_chain(manager, db, sv.engines, ep_id, ["keyframes"], RunOptions(shots=["S02"]))
    assert jobs(db)[-1][1:4] == ("image.continue_frame", ids[1], first)
