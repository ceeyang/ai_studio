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
    # 发给引擎的视频提示词：动作在前；「ANCHOR」里没有成像词，不加风格句
    assert prompt.startswith("she looks up slowly ") and "ANCHOR" not in prompt
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


def test_keyframe_force_with_several_candidates_keeps_selection(tmp_path, db):
    """force 一次出多张候选：不抢选定（否则最后完成的那张随机胜出），靠「N 张新候选」角标让用户自己挑。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    first = run_keyframe(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    run_keyframe(ctx, {"shot_id": shot_id, "force": True, "candidates": 2})
    run_keyframe(ctx, {"shot_id": shot_id, "force": True, "candidates": 2})
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").id == first


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
ROOM = ("A cramped roadside motorcycle repair shop at night, oil-stained concrete floor, "
        "a rolling steel shutter half open. Bare bulbs hang from the beams.")


def test_continuity_clause_is_positive_and_names_the_location():
    """H3 曾在镜头中途切到定妆照。连续性要求用正面写法、点名这一镜的地点；不写「no studio background」
    之类的否定（会把影棚背景招进画面）。"""
    from backend.pipeline.prompting import continuity_clause, location_summary

    loc = location_summary(ROOM)
    assert loc == ("a cramped roadside motorcycle repair shop at night, oil-stained concrete floor, "
                   "a rolling steel shutter half open")
    c = continuity_clause(loc)
    assert c == (f"One continuous take inside {loc}; the background stays this same place for the "
                 "whole clip, matching the first frame, as a single uninterrupted shot.")
    assert "no " not in c.lower().replace("not", "")
    low = c.lower()
    assert "studio" not in low and "portrait" not in low and "reference" not in low
    assert continuity_clause("") == ("One continuous take; the background stays the same place "
                                     "shown in the first frame for the whole clip, as a single "
                                     "uninterrupted shot.")
    # 没冠词、句首大写的场景外观：补冠词、小写
    assert location_summary("Narrow suburban Chinese e-bike repair shop at night, shutter down.")         == "a narrow suburban Chinese e-bike repair shop at night, shutter down"
    assert location_summary("Old tea house with lanterns.") == "an old tea house with lanterns"
    assert location_summary("LED-lit corridor.") == "a LED-lit corridor"
    assert location_summary("直播间") == ""            # 中文的场景名不能进英文提示词
    assert len(location_summary("word " * 80).split()) <= 30


def test_video_prompt_has_continuity_clause_without_changing_hash(tmp_path, db):
    """连续性要求点名这一镜所在段落的场景外观，拼在 Audio 段前面；不进输入哈希，已有视频不会全部过期。"""
    from backend.domain.models import Episode, Location, SceneBlock
    from backend.pipeline.prompting import continuity_clause, location_summary, video_input_hash

    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        shot = s.get(Shot, shot_id)
        ep = s.get(Episode, shot.episode_id)
        loc = Location(project_id=ep.project_id, name="修车铺", visual_prompt=ROOM)
        s.add(loc)
        s.flush()
        blk = SceneBlock(episode_id=ep.id, order=0, location_id=loc.id, text="甲")
        s.add(blk)
        s.flush()
        shot.scene_block_id = blk.id
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    prompt = runner.calls[-1].workflow["6"]["inputs"]["prompt"]
    body, audio = prompt.split("\nAudio:", 1)
    clause = continuity_clause(location_summary(ROOM))
    assert body.endswith(clause) and clause not in audio
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert "One continuous take" not in v.params["inputs"]["prompt"]
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


def test_video_cancel_during_scene_check_still_registers_candidate(tmp_path, db):
    """视频已经出好、在做跳切检测时被取消：GPU 的活不能白干，先把视频登记成候选再报取消。"""
    from backend.queue.errors import JobCanceled

    ctx, runner, shot_id = setup(tmp_path, db)
    ctx.services.ffmpeg = CutFFmpeg(fail=JobCanceled())
    run_keyframe(ctx, {"shot_id": shot_id})
    with pytest.raises(JobCanceled):
        run_video(ctx, {"shot_id": shot_id})
    with db.session() as s:
        video_slot = next(sl for sl in slots_for(s, "shot", shot_id) if sl.kind == "video")
        assert len(video_slot.candidates) == 1
        c = video_slot.candidates[0]
        assert "qc" not in c.params and ctx.services.media.abs(c.file_path).exists()
    assert any("跳切检测时任务被取消" in m for m in ctx.logs)


# ---------------------------------------------------------------- 视频提示词里的说话人
def test_short_ref_from_visual_prompt():
    from backend.pipeline.prompting import short_ref

    assert short_ref("a woman in her late twenties with an oval face, slim build") == \
        "the woman in her late twenties"
    assert short_ref("Chinese man in his mid-fifties with a weathered square face.") == \
        "the Chinese man in his mid-fifties"
    assert short_ref("") == "the speaker" and short_ref("老周，五十多岁") == "the speaker"


def test_video_dialogue_lines_use_english_refs_and_off_screen_voice():
    from backend.pipeline.prompting import Speaker

    dlg = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2},
           {"speaker": "老周", "zh": "我早就知道", "start": 2.2, "end": 3.5}]
    speakers = {"林夏": Speaker("the woman in her late twenties", True),
                "老周": Speaker("the stocky man", False)}
    v = compose_video_prompt("she looks up", "img", "quiet hum", dlg, anchor="", has_audio=True,
                             speakers=speakers)
    audio = v.split("\nAudio: ", 1)[1]
    assert 'the woman in her late twenties says in Mandarin Chinese: "词被改了"' in audio
    assert ('an off-screen voice says in Mandarin Chinese: "我早就知道", '
            "the on-screen character's lips stay closed") in audio
    assert "林夏" not in v and "老周" not in v
    # 不给 speakers（算输入哈希用）：和以前一字不差，已有视频不会因此过期
    old = compose_video_prompt("she looks up", "img", "quiet hum", dlg[:1], anchor="A",
                               has_audio=True)
    assert old == 'A she looks up\nAudio: quiet hum 林夏 says in Mandarin Chinese: "词被改了"'


def test_video_job_sends_speaker_refs_but_hash_unchanged(tmp_path, db):
    from backend.domain.models import Character, Episode
    from backend.pipeline.prompting import video_input_hash

    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        shot = s.get(Shot, shot_id)
        ep = s.get(Episode, shot.episode_id)
        lin = Character(project_id=ep.project_id, name="林夏",
                        visual_prompt="a woman in her late twenties with an oval face")
        zhou = Character(project_id=ep.project_id, name="老周",
                         visual_prompt="a stocky man in his fifties with grey stubble")
        s.add_all([lin, zhou])
        s.flush()
        shot.cast = [{"character_id": lin.id, "variation_id": None}]
        shot.dialogue = [{"speaker": "老周", "zh": "我早就知道", "start": 0.5, "end": 2.0}]
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    prompt = runner.calls[-1].workflow["6"]["inputs"]["prompt"]
    assert 'an off-screen voice says in Mandarin Chinese: "我早就知道"' in prompt
    assert "老周" not in prompt
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        shot, _, proj, style = load_shot(s, shot_id)
        assert v.input_hash == video_input_hash(shot, proj, style,
                                                v.params["first_frame_candidate_id"], "h3_video", True)


# ---------------------------------------------------------------- 身份来源：首帧没锁脸时视频不带定妆照
def _with_portrait(db, shot_id, image_engine="zimage"):
    from backend.domain.characters import add_portrait
    from backend.domain.models import Character

    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        proj.image_engine = image_engine
        ch = Character(project_id=proj.id, name="老周", visual_prompt="a stocky man in his fifties")
        s.add(ch)
        s.flush()
        add_portrait(s, ch.id, file_path="demo/media/portrait/a.png", engine="zimage", params={},
                     input_hash="p", job_id=None)
        shot.cast = [{"character_id": ch.id, "variation_id": None}]
        return ch.id


def test_video_skips_portrait_refs_when_first_frame_was_text_only(tmp_path, db):
    """zimage 首帧没吃参考图（脸来自文字）：H3 视频不再同时拿到定妆照里的另一张脸，身份以首帧为准。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    _with_portrait(db, shot_id, "zimage")
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        ff = selected_candidate(s, "shot", shot_id, "first_frame")
        assert ff.params["inputs"]["refs"] == []
    run_video(ctx, {"shot_id": shot_id})
    call = runner.calls[-1]
    assert call.meta["refs"] == [] and not any(k.startswith("ref_") for k in call.workflow)
    assert any("身份以首帧为准" in m and "老周 定妆照" in m for m in ctx.logs)
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.params["identity"] == "first_frame" and v.params["inputs"]["refs"] == []


def test_video_keeps_portrait_refs_when_first_frame_locked_face(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _with_portrait(db, shot_id, "h3_image")
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "first_frame").params["inputs"]["refs"]
    run_video(ctx, {"shot_id": shot_id})
    call = runner.calls[-1]
    assert len(call.meta["refs"]) == 1
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert v.params["identity"] == "refs" and len(v.params["inputs"]["refs"]) == 1


def test_legacy_video_made_with_refs_is_not_stale_under_new_policy(tmp_path, db, api=None):
    """老视频是按「无条件带定妆照」出的：换成新规则后不判过期、不自动重出（GPU 不白跑），重跑时跳过。"""
    from backend.app.views import shot_detail
    from backend.domain.slots import add_candidate, get_or_create_slot
    from backend.pipeline.prompting import video_input_hash

    ctx, runner, shot_id = setup(tmp_path, db)
    _with_portrait(db, shot_id, "zimage")
    run_keyframe(ctx, {"shot_id": shot_id})
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        from backend.domain.refs import shot_refs
        legacy = [r.candidate_id for r in shot_refs(s, shot)]
        ff = selected_candidate(s, "shot", shot_id, "first_frame")
        h_old = video_input_hash(shot, proj, style, ff.id, "h3_video", True, legacy)
        add_candidate(s, get_or_create_slot(s, "shot", shot_id, "video"), file_path="x.mp4",
                      engine="h3_video", params={}, input_hash=h_old, job_id=None)
    assert run_video(ctx, {"shot_id": shot_id}) == {"skipped": True, "candidate_ids": []}
    with db.session() as s:
        shot, _, proj, style = load_shot(s, shot_id)
        d = shot_detail(s, ctx.services, shot, proj, style)
        assert not next(x for x in d.slots if x.kind == "video").stale


# ---------------------------------------------------------------- 视频提示词：动作在前、风格一句在后
ANCHOR6 = ("Photorealistic live-action cinematic still, Chinese urban-fringe drama, shot on a 35mm "
           "anamorphic lens with shallow depth of field, subtle Kodak Vision3 500T film grain. "
           "The palette sets warm tungsten amber interiors against cold teal-blue rainy night "
           "exteriors. Lighting is motivated and practical: bare incandescent bulbs, wet reflections "
           "on concrete. Skin looks real, with visible pores.")


def test_video_style_is_short_and_has_no_still_or_scene_words():
    from backend.pipeline.prompting import video_style

    v = video_style(ANCHOR6)
    assert v.startswith("Visual style: ") and v.endswith(".")
    import re
    low = v.lower()
    assert not re.search(r"\b(still|rain\w*|bulbs?|drama)\b", low), v
    assert "35mm" in low and "footage" in low
    assert len(v.split()) <= 2 + 25
    assert video_style("") == "" and video_style("ANCHOR") == ""


def test_engine_video_prompt_puts_motion_first_and_keeps_hash_version():
    from backend.pipeline.prompting import Speaker

    dlg = [{"speaker": "林夏", "zh": "词被改了", "start": 0.5, "end": 2}]
    sent = compose_video_prompt("The young woman looks up slowly. Slow push in.", "img", "hum", dlg,
                                anchor=ANCHOR6, has_audio=True,
                                speakers={"林夏": Speaker("the young woman", True)})
    body = sent.split(chr(10) + "Audio:")[0]
    assert body.startswith("The young woman looks up slowly. Slow push in. Visual style: ")
    assert "cinematic still" not in sent and "bulbs" not in sent
    # 算哈希的老写法不变：锚整段在前
    old = compose_video_prompt("she looks up", "img", "hum", dlg, anchor=ANCHOR6, has_audio=True)
    assert old.startswith(ANCHOR6 + " she looks up")


def test_flagged_video_does_not_replace_clean_selection(tmp_path, db):
    """出片自检疑似跳切的新视频，不抢干净、没过期的选定（重拍也一样），候选照样登记、写 note。"""
    ctx, runner, shot_id = setup(tmp_path, db)
    ctx.services.ffmpeg = CutFFmpeg(cuts=[])
    run_keyframe(ctx, {"shot_id": shot_id})
    clean = run_video(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    ctx.services.ffmpeg = CutFFmpeg(cuts=[2.04])
    bad = run_video(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == clean
        video_slot = next(sl for sl in slots_for(s, "shot", shot_id) if sl.kind == "video")
        assert "疑似跳切" in next(c for c in video_slot.candidates if c.id == bad).note
    assert any("没有替换当前选定的视频" in m for m in ctx.logs)
    # 当前选定本身也疑似跳切：新的一条（重拍）照常选中
    newer = run_video(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == clean
        from backend.domain.slots import select_candidate
        select_candidate(s, bad)
    latest = run_video(ctx, {"shot_id": shot_id, "force": True})["candidate_ids"][0]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == latest != newer


def test_video_frames_keep_last_dialogue_line(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        sh = s.get(Shot, shot_id)
        sh.duration = 5.5
        sh.dialogue = [{"speaker": "林夏", "zh": "词被改了", "start": 4.0, "end": 5.3}]
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    assert runner.calls[-1].meta["frames"] == 141   # 124 帧（5.17s）会截掉 5.3s 结束的台词


def test_engine_video_prompt_keeps_eyes_off_the_lens():
    """第二次样片 S03：人物抬头被 H3 理解成看镜头。画面里有人时写明视线，剧本让他看镜头时不写。"""
    from backend.pipeline.prompting import GAZE_LINE, Speaker

    on = {"林夏": Speaker("the young woman", True)}
    off = {"林夏": Speaker("the young woman", False)}
    v = compose_video_prompt("The young woman glances up.", "img", "", [], anchor="", has_audio=True,
                             speakers=on)
    assert GAZE_LINE in v
    v = compose_video_prompt("The young woman looks straight into the camera.", "img", "", [],
                             anchor="", has_audio=True, speakers=on)
    assert GAZE_LINE not in v
    assert GAZE_LINE not in compose_video_prompt("Rain falls.", "img", "", [], anchor="",
                                                 has_audio=True, speakers=off)
    # 算哈希的老写法不加
    assert GAZE_LINE not in compose_video_prompt("x", "img", "", [], anchor="", has_audio=True)
