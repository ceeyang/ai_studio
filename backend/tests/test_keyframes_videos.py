import pytest

from backend.domain.models import Project, Shot, StyleBible
from backend.domain.slots import selected_candidate, slots_for
from backend.engines.base import upload_ref
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.prompting import (compose_image_prompt, compose_video_prompt, join_negative,
                                        shot_seed)
from backend.pipeline.videos import run_video
from backend.queue.errors import JobError
from backend.tests.helpers import FakeCtx, FakeRunner, make_services, seed_episode

IMG = "medium close-up, 85mm. a woman reads a teleprompter in a dark studio"


def test_prompt_helpers():
    assert compose_image_prompt("shot", "ANCHOR") == "ANCHOR shot"
    assert compose_image_prompt("ANCHOR shot", "ANCHOR") == "ANCHOR shot"
    assert compose_image_prompt("shot", "") == "shot"
    dlg = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2}]
    v = compose_video_prompt("she looks up", "img", "quiet hum", dlg, anchor="A", has_audio=True)
    assert v == 'A she looks up\nAudio: quiet hum 林夏 says in Mandarin Chinese: "词被改了"'
    assert compose_video_prompt("", "img", "hum", dlg, anchor="", has_audio=False) == "img"
    assert join_negative(" a ", "", None, "b") == "a, b"
    assert len({shot_seed(12345, 3, n) for n in range(4)}) == 4


def setup(tmp_path, db, video_engine="h3_video"):
    runner = FakeRunner()
    sv = make_services(tmp_path, db, comfy=runner)
    proj_id, ep_id = seed_episode(db, video_engine=video_engine)
    with db.session() as s:
        proj = s.get(Project, proj_id)
        proj.style_bible = StyleBible(anchor_text="ANCHOR", negative="cartoon")
        proj.negative = "blurry"
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt=IMG,
                  motion_prompt="she looks up slowly", audio_prompt="quiet hum",
                  dialogue=[{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2.0}])
        s.add(sh)
        s.flush()
        shot_id = sh.id
    return FakeCtx(sv, job_id=11), runner, shot_id


def test_keyframe_creates_selected_candidate(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    res = run_keyframe(ctx, {"shot_id": shot_id})
    call = runner.calls[0]
    assert call.workflow["5"]["inputs"]["text"] == "ANCHOR " + IMG
    assert call.workflow["6"]["inputs"]["text"] == "blurry, cartoon"
    assert call.workflow["10"]["inputs"]["filename_prefix"] == "studio/demo/kf_S01"
    with db.session() as s:
        c = selected_candidate(s, "shot", shot_id, "first_frame")
        assert c.id == res["candidate_ids"][0] and c.engine == "zimage" and c.job_id == 11
        assert c.file_path.startswith("demo/media/first_frame/")
        assert c.params["workflow"] == "demo/runs/11/workflow.api.json"
        assert ctx.services.media.abs(c.file_path).exists()
    assert ctx.progress_calls == [(1, 1)]


def test_second_keyframe_keeps_selection_and_changes_seed(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})["candidate_ids"][0]
    run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})
    assert runner.calls[0].meta["seed"] != runner.calls[1].meta["seed"]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first
        assert len(slots_for(s, "shot", shot_id)[0].candidates) == 2


def test_rerun_after_prompt_change_selects_new(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        s.get(Shot, shot_id).image_prompt = IMG + ", rain on the window"
    new = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == new


def test_video_requires_first_frame(tmp_path, db):
    ctx, _, shot_id = setup(tmp_path, db)
    with pytest.raises(JobError, match="还没有选定首帧"):
        run_video(ctx, {"shot_id": shot_id})


def test_h3_video_prompt_has_audio_and_uses_selected_frame(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    call = runner.calls[-1]
    prompt = call.workflow["6"]["inputs"]["prompt"]
    assert prompt.startswith("ANCHOR she looks up slowly ")
    assert "\nAudio: quiet hum" in prompt
    assert "词被改了" in prompt
    with db.session() as s:
        ff = selected_candidate(s, "shot", shot_id, "first_frame")
        assert call.workflow["5"]["inputs"]["image"] == upload_ref(ctx.services.media.abs(ff.file_path))
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.id == res["candidate_ids"][0] and v.file_path.endswith(".mp4")


def test_wan_video_has_no_audio_section(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db, video_engine="wan22_i2v")
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    assert "Audio:" not in runner.calls[-1].workflow["9"]["inputs"]["text"]
    assert runner.calls[-1].model_family == "wan22"


def test_missing_comfy_raises(tmp_path, db):
    sv = make_services(tmp_path, db, comfy=None)
    _, ep_id = seed_episode(db)
    with db.session() as s:
        sh = Shot(episode_id=ep_id, order=0, code="S01", image_prompt=IMG)
        s.add(sh)
        s.flush()
        sid = sh.id
    with pytest.raises(JobError, match="ComfyUI"):
        run_keyframe(FakeCtx(sv), {"shot_id": sid})


def test_keyframe_rerun_unchanged_is_skipped(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id, "candidates": 1})["candidate_ids"][0]
    res = run_keyframe(ctx, {"shot_id": shot_id, "candidates": 1, "force": False})
    assert res == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == 1
    assert any("跳过" in m for m in ctx.logs)
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first


def test_keyframe_force_regenerates(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_keyframe(ctx, {"shot_id": shot_id, "force": True})
    assert len(runner.calls) == 2 and len(res["candidate_ids"]) == 1


def test_keyframe_prompt_change_regenerates(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        s.get(Shot, shot_id).image_prompt = IMG + ", rain"
    assert run_keyframe(ctx, {"shot_id": shot_id}).get("skipped") is None
    assert len(runner.calls) == 2


def test_keyframe_multiple_candidates_still_generate(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})
    run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})
    assert len(runner.calls) == 3


def test_video_rerun_skip_force_and_new_first_frame(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    assert run_video(ctx, {"shot_id": shot_id}) == {"skipped": True, "candidate_ids": []}
    assert len(runner.calls) == 2
    assert len(run_video(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"]) == 1
    assert len(runner.calls) == 3
    # 新首帧（输入变了）→ 视频 hash 变了，要重出
    with db.session() as s:
        s.get(Shot, shot_id).image_prompt = IMG + ", rain"
    run_keyframe(ctx, {"shot_id": shot_id})
    assert run_video(ctx, {"shot_id": shot_id}).get("skipped") is None
    assert len(runner.calls) == 5
    # 只改运动提示词也会重出
    with db.session() as s:
        s.get(Shot, shot_id).motion_prompt = "she turns away"
    assert run_video(ctx, {"shot_id": shot_id}).get("skipped") is None


def test_keyframe_force_retake_selects_new_candidate_even_with_same_input(tmp_path, db):
    """force 重拍是用户明确要换一条：输入没变（input_hash 一样）也要选中新候选。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    new = run_keyframe(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    assert new != first
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == new


def test_keyframe_non_forced_extra_candidate_keeps_selection(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    run_keyframe(ctx, {"shot_id": shot_id, "candidates": 2})
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first


def test_video_force_retake_selects_new_candidate_even_with_same_input(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    first = run_video(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    new = run_video(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    assert new != first
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == new
        video_slot = next(sl for sl in slots_for(s, "shot", shot_id) if sl.kind == "video")
        assert len({c.input_hash for c in video_slot.candidates}) == 1   # 确实是同一输入


def test_keyframe_logs_when_engine_ignores_portraits(tmp_path, db):
    """zimage 不吃参考图：任务日志写明定妆照用不上、人物一致只靠文字，提示可换 H3 图像。"""
    from backend.domain.characters import add_portrait
    from backend.domain.models import Character

    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        ch = Character(project_id=proj.id, name="老周", visual_prompt="a man")
        s.add(ch)
        s.flush()
        add_portrait(s, ch.id, file_path="demo/media/portrait/a.png", engine="zimage", params={},
                     input_hash="p", job_id=None)
        shot.cast = [{"character_id": ch.id, "variation_id": None}]
    run_keyframe(ctx, {"shot_id": shot_id})
    hint = [m for m in ctx.logs if "不吃参考图" in m]
    assert hint and "老周 定妆照" in hint[0] and "H3 图像" in hint[0]


def test_keyframe_no_ref_hint_without_portraits(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    assert not any("不吃参考图" in m for m in ctx.logs)


# ---------------------------------------------------------------- 视频连续性：提示词 + 出片自检
def test_video_prompt_has_continuity_clause_without_changing_hash(tmp_path, db):
    """H3 曾在镜头中途切到定妆照：提示词里写明单个连续镜头、和首帧同一地点、不切镜、不要影棚背景。
    这句不进输入哈希，已有视频不会因为模板更新全部变成过期。"""
    from backend.pipeline.prompting import CONTINUITY_CLAUSE, video_input_hash

    ctx, runner, shot_id = setup(tmp_path, db)
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    prompt = runner.calls[-1].workflow["6"]["inputs"]["prompt"]
    body, audio = prompt.split("\nAudio:", 1)
    assert CONTINUITY_CLAUSE in body and CONTINUITY_CLAUSE not in audio
    low = CONTINUITY_CLAUSE.lower()
    assert "continuous" in low and "same location" in low and "no cut" in low and "studio" in low
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert CONTINUITY_CLAUSE not in v.params["inputs"]["prompt"]
        shot, _, proj, style = load_shot(s, shot_id)
        assert v.input_hash == video_input_hash(shot, proj, style,
                                                v.params["first_frame_candidate_id"], "h3_video", True)


class CutFFmpeg:
    """假 ffmpeg：只有场景检测，返回预设的硬切时间点（或报错）。"""

    def __init__(self, cuts=None, fail=None):
        self.cuts, self.fail, self.seen = cuts or [], fail, []

    def scene_cuts(self, path):
        self.seen.append(path)
        if self.fail:
            raise self.fail
        return list(self.cuts)


def video_with_ffmpeg(tmp_path, db, ff):
    ctx, runner, shot_id = setup(tmp_path, db)
    ctx.services.ffmpeg = ff
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    return ctx, shot_id, res


def test_video_qc_flags_hard_cut_on_candidate(tmp_path, db):
    ff = CutFFmpeg(cuts=[2.04])
    ctx, shot_id, res = video_with_ffmpeg(tmp_path, db, ff)
    assert res["qc"] == {"hard_cuts": [2.04]}
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert "疑似跳切" in v.note and "2.0" in v.note
        assert v.params["qc"]["hard_cuts"] == [2.04]
        assert ctx.services.media.abs(v.file_path) == ff.seen[0]
    assert any("疑似跳切" in m and "S01" in m for m in ctx.logs)


def test_video_qc_clean_clip_has_no_note(tmp_path, db):
    ctx, shot_id, res = video_with_ffmpeg(tmp_path, db, CutFFmpeg(cuts=[]))
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.note == "" and v.params["qc"] == {"hard_cuts": []}
    assert not any("疑似跳切" in m for m in ctx.logs)


def test_video_qc_failure_does_not_fail_job(tmp_path, db):
    from backend.engines.ffmpeg import FFmpegError

    ctx, shot_id, res = video_with_ffmpeg(tmp_path, db, CutFFmpeg(fail=FFmpegError("坏文件")))
    assert res["candidate_ids"]
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.note == "" and "qc" not in v.params
    assert any("跳切检测没跑成" in m for m in ctx.logs)
