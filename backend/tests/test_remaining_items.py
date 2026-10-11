"""审查报告剩余条目：P1-5 自动重拍、P1-7 只出声的角色、P1-9 视频参考图减量、P1-12 剧本重写认人、
P2-5 三视图按画风、P2-6 首帧先写景别、P2-7 建议接上一镜、P2-8 中文字幕对齐实际开口、P2-9 镜头节奏。"""
from __future__ import annotations

from backend.domain.models import Project
from backend.domain.slots import selected_candidate, slots_for
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.videos import run_video
from backend.tests.test_keyframes_videos import SsimFFmpeg, setup


# ---------------------------------------------------------------- P1-5 开头没接上首帧：自动重拍一次
class SeqSsim(SsimFFmpeg):
    """首帧相似度按顺序返回 values 里的值（最后一个重复用）。"""

    def __init__(self, values):
        super().__init__()
        self.values = list(values)

    def first_frame_ssim(self, video, image):
        self.compared.append((video, image))
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _auto_retry(db, shot_id, on=True):
    with db.session() as s:
        _, _, proj, _ = load_shot(s, shot_id)
        proj.auto_retry_video = on


def test_auto_retry_reshoots_once_with_new_seed_and_selects_the_clean_take(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.92])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    videos = [c for c in runner.calls if "6" in c.workflow and "prompt" in c.workflow["6"]["inputs"]]
    assert len(videos) == 2 and videos[0].meta["seed"] != videos[1].meta["seed"]
    assert len(res["candidate_ids"]) == 2
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.id == res["candidate_ids"][1] and v.note == ""
        assert v.params["retry_of"] == res["candidate_ids"][0]
        first = next(c for sl in slots_for(s, "shot", shot_id) if sl.kind == "video"
                     for c in sl.candidates if c.id == res["candidate_ids"][0])
        assert "开头没接上首帧" in first.note
    assert any("自动重拍" in m for m in ctx.logs)


def test_auto_retry_only_once_even_if_retake_also_mismatches(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.2])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 2
    assert len(runner.calls) == 3   # 首帧 1 次 + 视频 2 次
    with db.session() as s:
        # 两条都没接上：选定第一条（和以前一样），第二条不抢
        assert selected_candidate(s, "shot", shot_id, "video").id == res["candidate_ids"][0]


def test_no_auto_retry_when_project_setting_off(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    ctx.services.ffmpeg = SeqSsim([0.3, 0.92])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 1 and len(runner.calls) == 2


def test_no_auto_retry_for_clean_or_cut_only_take(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _auto_retry(db, shot_id)
    ctx.services.ffmpeg = SeqSsim([0.9])
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert len(res["candidate_ids"]) == 1
    with db.session() as s:
        assert db is not None and s.get(Project, 1).auto_retry_video


# ---------------------------------------------------------------- P1-9 视频每人只带一张脸
def test_video_brings_one_face_cell_per_character(tmp_path, db):
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "body")
    tt.gen_sheet(ctx, ids["chen"], "face")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    face = tt.cell(sv, ids["chen"], "face_front")[0]
    assert runner.calls[-1].meta["refs"] == [str(sv.media.abs(face.file_path))]
    with db.session() as s:
        v = selected_candidate(s, "shot", ids["shot"], "video")
        assert [r["label"] for r in v.params["inputs"]["refs"]] == ["陈师傅 三视图·正脸"]
    assert not tt.stale(sv, ids["shot"], "video")


def test_video_without_face_cells_brings_only_the_portrait(tmp_path, db):
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    _, chen_path = tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "body")       # 全身格子不进视频
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    run_video(ctx, {"shot_id": ids["shot"]})
    assert runner.calls[-1].meta["refs"] == [str(chen_path)]


def test_old_video_with_portrait_and_cell_is_not_stale_and_skipped(tmp_path, db):
    """P1-9 之前出的视频（每人定妆照 + 三视图格）：换规则后不判过期、不白跑 GPU。"""
    from backend.app.views import shot_detail
    from backend.domain.refs import legacy_video_refs
    from backend.domain.slots import add_candidate, get_or_create_slot
    from backend.pipeline.prompting import video_input_hash
    from backend.tests import test_turnaround as tt

    ctx, runner, ids = tt.setup(tmp_path, db, image_engine="h3_image")
    sv = ctx.services
    tt.put_portrait(sv, ids["chen"])
    tt.gen_sheet(ctx, ids["chen"], "face")
    run_keyframe(ctx, {"shot_id": ids["shot"]})
    with db.session() as s:
        shot, _, proj, style = load_shot(s, ids["shot"])
        old = [r.candidate_id for r in legacy_video_refs(s, shot)]
        assert len(old) == 2
        ff = selected_candidate(s, "shot", ids["shot"], "first_frame")
        h_old = video_input_hash(shot, proj, style, ff.id, "h3_video", True, old)
        add_candidate(s, get_or_create_slot(s, "shot", ids["shot"], "video"), file_path="x.mp4",
                      engine="h3_video", params={}, input_hash=h_old, job_id=None)
    n = len(runner.calls)
    assert run_video(ctx, {"shot_id": ids["shot"]}) == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == n
    with db.session() as s:
        shot, _, proj, style = load_shot(s, ids["shot"])
        d = shot_detail(s, sv, shot, proj, style)
        assert not next(x for x in d.slots if x.kind == "video").stale


def test_project_settings_pace_and_auto_retry_via_api(api):
    api.post("/api/projects", json={"title": "demo"})
    r = api.get("/api/projects/demo").json()
    assert r["shot_seconds"] is None and r["auto_retry_video"] is False
    r = api.patch("/api/projects/demo", json={"shot_seconds": 3, "auto_retry_video": True})
    assert r.status_code == 200
    assert r.json()["shot_seconds"] == 3 and r.json()["auto_retry_video"] is True
    r = api.patch("/api/projects/demo", json={"shot_seconds": 0})
    assert r.json()["shot_seconds"] is None and r.json()["auto_retry_video"] is True
    assert api.patch("/api/projects/demo", json={"shot_seconds": 7}).status_code in (400, 422)
