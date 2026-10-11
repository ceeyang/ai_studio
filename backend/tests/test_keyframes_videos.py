import pytest

from backend.domain.models import Project, Shot, StyleBible
from backend.domain.slots import selected_candidate, slots_for
from backend.engines.base import upload_ref
from backend.pipeline.keyframes import run_keyframe
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
    assert prompt.startswith("ANCHOR she looks up slowly\nAudio: quiet hum")
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
