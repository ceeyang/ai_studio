"""第四次实跑（深夜便利店·验收四）报告里的问题：画外说话人叫法、视频提示词里的「completely empty」、
场景图风格句里的人物用语、只出声角色的剧本写法、画外男声念成女声的音高粗检。"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from backend.domain.models import Character
from backend.domain.slots import selected_candidate
from backend.pipeline.keyframes import load_shot, run_keyframe
from backend.pipeline.prompting import Speaker, location_summary, voice_refs
from backend.pipeline.videos import _speakers, qc_bad, qc_note, run_video
from backend.post.speech import median_pitch, voice_gender_mismatch
from backend.tests.test_keyframes_videos import SsimFFmpeg, setup

LIN = ("Chinese woman in her mid-twenties, oval face, low black ponytail. Wears a dark green convenience "
       "store uniform vest over a plain grey cotton crewneck long-sleeve top.")
CHEN = ("Chinese man in his early thirties, lean angular face. Wears a dark charcoal-grey hooded "
        "technical shell jacket over a black cotton t-shirt and a black baseball cap.")
BOSS = "Chinese man in his early fifties, voice only"
S04_IMG = ("Close-up, 85mm, eye level. {{char:17}} The young woman stands behind the counter, facing left, "
           "lips closed.")
S04_AUDIO = ("Off-screen voice of the stocky man from the back room, muffled and puzzled, calling out a "
             "question. Steady hum of fluorescent tubes.")
S05_IMG = ("Medium close-up, 50mm. {{char:18}} The lean man stands with the cap pressed to his chest.")
S05_AUDIO = ("Off-screen voice of the young woman, cold and tense, asking a sharp question. The lean man "
             "answers in a low, hoarse, measured voice.")


def _proj():
    return SimpleNamespace(characters=[
        SimpleNamespace(id=17, name="林晚", visual_prompt=LIN),
        SimpleNamespace(id=18, name="陈默", visual_prompt=CHEN),
        SimpleNamespace(id=19, name="店长", visual_prompt=BOSS)])


# ---------------------------------------------------------------- 1. 画外说话人用分镜里的叫法
def test_off_screen_speaker_uses_ref_from_this_shots_audio_prompt():
    shot = SimpleNamespace(image_prompt=S04_IMG, audio_prompt=S04_AUDIO, motion_prompt="",
                           cast=[{"character_id": 17}],
                           dialogue=[{"speaker": "店长", "zh": "小林，监控怎么黑了？", "start": 0.4, "end": 2.4}])
    sp = _speakers(_proj(), shot)
    assert sp["店长"] == Speaker("the stocky man", False, "male")
    assert sp["林晚"] == Speaker("the young woman", True, "female")


def test_off_screen_speaker_ref_skips_names_taken_by_on_screen_people():
    shot = SimpleNamespace(image_prompt=S05_IMG, audio_prompt=S05_AUDIO, motion_prompt="",
                           cast=[{"character_id": 18}],
                           dialogue=[{"speaker": "林晚", "zh": "你到底是谁？", "start": 0.3, "end": 1.6},
                                     {"speaker": "陈默", "zh": "是打给我的。", "start": 2.2, "end": 4.6}])
    sp = _speakers(_proj(), shot)
    assert sp["林晚"] == Speaker("the young woman", False, "female")
    assert sp["陈默"] == Speaker("the lean man", True, "male")


def test_off_screen_speaker_falls_back_to_episode_placeholder_then_appearance():
    other = SimpleNamespace(image_prompt="Wide shot. {{char:17}} The young woman wipes the counter.")
    shot = SimpleNamespace(image_prompt=S05_IMG, audio_prompt="Fridge hum.", motion_prompt="",
                           cast=[{"character_id": 18}],
                           dialogue=[{"speaker": "林晚", "zh": "你到底是谁？", "start": 0.3, "end": 1.6}])
    sp = _speakers(_proj(), shot, [other, shot])
    assert sp["林晚"].ref == "the young woman"
    # 哪儿都没写：退回外貌第一分句
    assert _speakers(_proj(), shot)["林晚"].ref == "the Chinese woman in her mid-twenties"


def test_off_screen_gender_comes_from_ref_when_appearance_has_none():
    proj = SimpleNamespace(characters=[SimpleNamespace(id=19, name="店长", visual_prompt="")])
    shot = SimpleNamespace(image_prompt="Close-up.", audio_prompt=S04_AUDIO, motion_prompt="", cast=[],
                           dialogue=[{"speaker": "店长", "zh": "小林？", "start": 0.4, "end": 1.4}])
    assert _speakers(proj, shot)["店长"] == Speaker("the stocky man", False, "male")


def test_voice_refs_ignore_function_words_and_gender_mismatch():
    refs = voice_refs("A cup rattles on the counter where the man leans. Voice of the tired old woman.")
    assert refs == ["the man", "the tired old woman"]


def test_engine_prompt_has_one_name_for_off_screen_voice(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        s.add(Character(project_id=proj.id, name="店长", visual_prompt=BOSS, voice_only=True))
        shot.audio_prompt = S04_AUDIO
        shot.dialogue = [{"speaker": "店长", "zh": "小林，监控怎么黑了？", "start": 0.4, "end": 2.4}]
    run_keyframe(ctx, {"shot_id": shot_id})
    run_video(ctx, {"shot_id": shot_id})
    prompt = runner.calls[-1].workflow["6"]["inputs"]["prompt"]
    assert "An off-screen male voice (the stocky man) says" in prompt
    assert "early fifties" not in prompt


# ---------------------------------------------------------------- 2. 场景摘要不写「空无一人」
@pytest.mark.parametrize("text,expected", [
    ("Small suburban 24-hour convenience store at three in the morning, completely empty.",
     "a small suburban 24-hour convenience store at three in the morning"),
    ("Empty interior of a narrow noodle shop at dusk.", "a narrow noodle shop at dusk"),
    ("A deserted bus stop on a rainy street, no people around, flickering lamp.",
     "a bus stop on a rainy street, flickering lamp"),
    ("Quiet unoccupied office floor with rows of desks.", "a quiet office floor with rows of desks"),
    ("Hospital corridor, silent and empty, green walls.", "a hospital corridor, silent, green walls"),
    ("Old tea house with lanterns.", "an old tea house with lanterns"),
])
def test_location_summary_drops_emptiness_words(text, expected):
    assert location_summary(text) == expected


# ---------------------------------------------------------------- 4. 场景图风格句不带人物用语
def test_location_style_drops_person_terms():
    from backend.pipeline.locations import location_style

    anchor = ("Photorealistic live-action cinematic still, shot on 35mm prime lenses, subtle fine film grain, "
              "natural skin texture with visible pores, natural lip color, no heavy makeup, realistic fabric "
              "weave and material detail, true-to-life proportions, soft directional light.")
    style = location_style(anchor).lower()
    for word in ("skin", "lip", "makeup", "proportions"):
        assert word not in style
    assert "film grain" in style and "material detail" in style and "soft directional light" in style


# ---------------------------------------------------------------- 5. 只出声的角色：剧本结构不要外貌
def test_structure_prompt_says_voice_only_description_has_no_looks():
    from backend.llm.prompts import PromptLibrary
    from backend.services import BUILTIN_DIR

    system, _ = PromptLibrary(BUILTIN_DIR, BUILTIN_DIR / "none").render(
        "script_structure", project_title="t", target_seconds=25, source_text="x", shot_count=5,
        shot_seconds=5, aspect="9:16")
    assert "不写外貌" in system


# ---------------------------------------------------------------- 6. 画外男声念成女声：音高粗检
SR = 16000


def _tone(f0: float, seconds: float, harmonics: int = 6, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in range(1, harmonics + 1))
    return (amp * x / np.max(np.abs(x))).astype(np.float32)


@pytest.mark.parametrize("f0", [95.0, 120.0, 150.0, 210.0, 240.0, 280.0])
def test_median_pitch_of_synthetic_voice(f0):
    got = median_pitch(_tone(f0, 1.5), SR)
    assert got is not None and abs(got - f0) / f0 < 0.05


def test_median_pitch_pure_sine_and_windows():
    x = np.concatenate([_tone(110.0, 1.0, harmonics=1), _tone(240.0, 1.0, harmonics=1)])
    assert abs(median_pitch(x, SR, [(0.0, 1.0)]) - 110) < 6
    assert abs(median_pitch(x, SR, [(1.0, 2.0)]) - 240) < 12


def test_median_pitch_unsure_returns_none():
    rng = np.random.default_rng(1)
    assert median_pitch(np.zeros(SR, dtype=np.float32), SR) is None               # 没声音
    assert median_pitch((0.3 * rng.standard_normal(SR)).astype(np.float32), SR) is None   # 噪声
    assert median_pitch(_tone(120, 1.0, amp=0.001), SR) is None                     # 太小声
    assert median_pitch(_tone(120, 0.1), SR) is None                                # 太短
    assert median_pitch(_tone(120, 2.0), SR, [(5.0, 6.0)]) is None                   # 窗口外


def test_voice_gender_mismatch_thresholds():
    assert voice_gender_mismatch(237.0, "male") and voice_gender_mismatch(120.0, "female")
    assert not voice_gender_mismatch(151.0, "male") and not voice_gender_mismatch(229.0, "female")
    assert not voice_gender_mismatch(190.0, "male") and not voice_gender_mismatch(150.0, "female")
    assert not voice_gender_mismatch(None, "male") and not voice_gender_mismatch(300.0, "")


class PitchFFmpeg(SsimFFmpeg):
    def __init__(self, pitch, fail=None):
        super().__init__(ssim=0.95)
        self.pitch, self.pitch_fail, self.windows = pitch, fail, []

    def voice_pitch(self, path, windows):
        self.windows.append(list(windows))
        if self.pitch_fail:
            raise self.pitch_fail
        return self.pitch


def _boss_shot(db, shot_id):
    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        s.add(Character(project_id=proj.id, name="店长", visual_prompt=BOSS, voice_only=True))
        shot.audio_prompt = S04_AUDIO
        shot.dialogue = [{"speaker": "店长", "zh": "小林，监控怎么黑了？", "start": 0.4, "end": 2.4}]


def test_video_qc_flags_male_line_spoken_by_female_voice(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _boss_shot(db, shot_id)
    ff = PitchFFmpeg(237.4)
    ctx.services.ffmpeg = ff
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    # 只有男声台词：时间窗前后各放宽 0.25 秒
    assert ff.windows == [[pytest.approx((0.15, 2.65))]]
    assert res["qc"]["voice_mismatch"] == [{"speakers": ["店长"], "expected": "male", "f0": 237.4}]
    with db.session() as s:
        v = selected_candidate(s, "shot", shot_id, "video")
        assert "疑似声音性别不对" in v.note and "店长" in v.note and "237" in v.note
    assert any("疑似声音性别不对" in m for m in ctx.logs)


def test_video_qc_voice_mismatch_does_not_take_selection(tmp_path, db):
    ctx, runner, shot_id = setup(tmp_path, db)
    _boss_shot(db, shot_id)
    ctx.services.ffmpeg = PitchFFmpeg(150.0)
    run_keyframe(ctx, {"shot_id": shot_id})
    first = run_video(ctx, {"shot_id": shot_id})["candidate_ids"][0]
    ctx.services.ffmpeg = PitchFFmpeg(250.0)
    run_video(ctx, {"shot_id": shot_id, "force": True})
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").id == first


@pytest.mark.parametrize("fail", [False, True])
def test_video_qc_voice_unsure_or_failed_is_not_flagged(tmp_path, db, fail):
    from backend.engines.ffmpeg import FFmpegError

    ctx, runner, shot_id = setup(tmp_path, db)
    _boss_shot(db, shot_id)
    ctx.services.ffmpeg = PitchFFmpeg(None, fail=FFmpegError("boom") if fail else None)
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert "voice_mismatch" not in res["qc"]
    with db.session() as s:
        assert selected_candidate(s, "shot", shot_id, "video").note == ""


def test_voice_lines_group_by_gender_without_padding_when_mixed(tmp_path, db):
    """一男一女两句台词：各按自己的时间窗查，不放宽（免得把对方的声音算进来）；只有女声那组对不上。"""
    class ByWindow(PitchFFmpeg):
        def voice_pitch(self, path, windows):
            self.windows.append(list(windows))
            return 120.0 if windows[0][0] < 1 else 110.0

    ctx, runner, shot_id = setup(tmp_path, db)
    with db.session() as s:
        shot, _, proj, _ = load_shot(s, shot_id)
        s.add(Character(project_id=proj.id, name="林晚", visual_prompt=LIN))
        s.add(Character(project_id=proj.id, name="陈默", visual_prompt=CHEN))
        shot.dialogue = [{"speaker": "陈默", "zh": "是她让我还给你的。", "start": 0.6, "end": 2.6},
                         {"speaker": "林晚", "zh": "她三年前就走了。", "start": 3.6, "end": 5.6}]
    ff = ByWindow(None)
    ctx.services.ffmpeg = ff
    run_keyframe(ctx, {"shot_id": shot_id})
    res = run_video(ctx, {"shot_id": shot_id})
    assert sorted(ff.windows) == [[(0.6, 2.6)], [(3.6, 5.6)]]
    assert res["qc"]["voice_mismatch"] == [{"speakers": ["林晚"], "expected": "female", "f0": 110.0}]


def test_qc_bad_and_note_include_voice_mismatch():
    qc = {"hard_cuts": [], "voice_mismatch": [{"speakers": ["店长"], "expected": "male", "f0": 237.4}]}
    assert qc_bad(qc)
    assert qc_note(qc).startswith("疑似声音性别不对：店长的台词应是男声")
