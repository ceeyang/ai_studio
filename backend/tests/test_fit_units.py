"""按高棉语时长改写中文：改写循环（假 TTS 时长 + 假 LLM）、重排台词时间、音频提示、说话起点检测、无原声混音命令。"""
import shutil
import subprocess

import numpy as np
import pytest

from backend.post.dub import build_dub_mix_cmd
from backend.post.fit import (FitLine, fit_lines, speech_chars, update_audio_prompt, retime_shot)
from backend.post.options import ExportOptions
from backend.post.speech import align_onsets, frames_to_segments, speech_segments_from_pcm


# ---------------------------------------------------------------- 改写循环
def line(i, zh, km_s, speaker="陈师傅"):
    return FitLine(id=f"S01.{i}", speaker=speaker, zh_original=zh, km="ក", km_seconds=km_s,
                   zh_voice="zh-CN-YunxiNeural")


def measure_by_chars(rate=4.0):
    """假 TTS：每个字 1/rate 秒。"""
    calls = []

    def measure(text, voice):
        calls.append((text, voice))
        return speech_chars(text) / rate
    return measure, calls


def test_fit_keeps_original_when_already_within_tolerance():
    measure, _ = measure_by_chars()
    asked = []
    out = fit_lines([line(1, "先让我看看", 1.3)], measure, lambda r, todo: asked.append(todo) or {},
                    tol=0.10, rounds=3)
    assert asked == []                                   # 原句 1.25s / 1.3s，不用问 LLM
    assert out[0].zh == "先让我看看" and out[0].zh_seconds == pytest.approx(1.25)
    assert out[0].ratio == pytest.approx(1.25 / 1.3, abs=1e-3) and out[0].rounds == 0


def test_fit_iterates_until_within_tolerance_and_passes_history():
    measure, _ = measure_by_chars()
    answers = [{"S01.1": "换？那要先看看"}, {"S01.1": "换？那得先让我好好看看"}]
    asked = []

    def rewrite(r, todo):
        asked.append((r, todo))
        return answers[r - 1]
    out = fit_lines([line(1, "换？", 2.5)], measure, rewrite, tol=0.10, rounds=3)
    assert len(asked) == 2                               # 第 2 轮 10 字 2.5s 就达标，不再问第 3 轮
    r1 = asked[0][1][0]
    assert r1["id"] == "S01.1" and r1["km_seconds"] == 2.5 and r1["target_chars"] == 10
    assert r1["tries"] == [{"zh": "换？", "seconds": 0.25}]
    assert asked[1][1][0]["tries"][-1] == {"zh": "换？那要先看看", "seconds": 1.5}
    assert out[0].zh == "换？那得先让我好好看看" and out[0].ratio == pytest.approx(1.0)
    assert out[0].rounds == 2


def test_fit_keeps_best_try_after_max_rounds():
    measure, _ = measure_by_chars()
    answers = [{"S01.1": "一二三四五六七八九十十一十二"}, {"S01.1": "一二三四五六七"},
               {"S01.1": "一二三四五六七八九十十一十二十三十四"}]
    out = fit_lines([line(1, "一二", 2.0)], measure, lambda r, todo: answers[r - 1],
                    tol=0.10, rounds=3)
    # 目标 2.0s（8 字）：14 字 3.5s、7 字 1.75s、18 字 4.5s → 最好的是 7 字（0.875）
    assert out[0].zh == "一二三四五六七" and out[0].ratio == pytest.approx(0.875)
    assert len(out[0].tries) == 4


def test_fit_only_asks_for_lines_still_out_of_tolerance():
    measure, _ = measure_by_chars()
    asked = []

    def rewrite(r, todo):
        asked.append([t["id"] for t in todo])
        return {t["id"]: "四个字了" for t in todo}
    fit_lines([line(1, "好的好的", 1.0), line(2, "嗯", 1.0)], measure, rewrite, rounds=3)
    assert asked == [["S01.2"]]


def test_fit_ignores_missing_or_empty_answers():
    measure, _ = measure_by_chars()
    out = fit_lines([line(1, "嗯", 1.0)], measure, lambda r, todo: {"S01.1": " "}, rounds=2)
    assert out[0].zh == "嗯" and len(out[0].tries) == 1


def test_speech_chars_ignores_punctuation_and_counts_latin_words_as_two():
    assert speech_chars("先告诉你修还是换，不收钱。") == 11
    assert speech_chars("Telegram发过来，") == 5


# ---------------------------------------------------------------- 重排台词时间
def test_retime_keeps_original_starts_and_extends_duration():
    dlg = [{"start": 1.6, "end": 4.45, "km_seconds": 3.19}]
    out, dur, over = retime_shot(dlg, 4.5, 10.0)
    assert out[0]["start"] == 1.6 and out[0]["end"] == pytest.approx(4.79)
    assert dur == pytest.approx(5.2) and over is False      # 4.79 + 0.4 尾巴，向上取到 0.1


def test_retime_enforces_head_margin_and_gaps():
    dlg = [{"start": 0.1, "end": 1.0, "km_seconds": 2.26},
           {"start": 2.1, "end": 3.3, "km_seconds": 1.27},
           {"start": 3.4, "end": 5.85, "km_seconds": 2.79}]
    out, dur, over = retime_shot(dlg, 6.0, 10.0)
    assert [d["start"] for d in out] == pytest.approx([0.4, 2.81, 4.23])
    assert [d["end"] for d in out] == pytest.approx([2.66, 4.08, 7.02])
    assert dur == pytest.approx(7.5) and not over


def test_retime_never_shrinks_duration():
    out, dur, _ = retime_shot([{"start": 1.0, "end": 2.0, "km_seconds": 0.5}], 6.0, 10.0)
    assert dur == 6.0 and out[0]["end"] == pytest.approx(1.5)


def test_retime_packs_lines_when_original_starts_overflow_engine_max():
    dlg = [{"start": 3.0, "km_seconds": 3.0}, {"start": 6.0, "km_seconds": 3.0}]
    out, dur, over = retime_shot(dlg, 5.0, 8.0)
    assert [d["start"] for d in out] == pytest.approx([0.4, 3.55])
    assert dur == pytest.approx(7.0) and not over


def test_retime_caps_at_engine_max_and_flags_overflow():
    out, dur, over = retime_shot([{"start": 0.4, "km_seconds": 9.9}], 5.0, 10.0)
    assert dur == 10.0 and over is True


# ---------------------------------------------------------------- 音频提示
def test_audio_prompt_swaps_lines_and_replaces_pacing():
    old = 'Chen says "能修的" calmly. No music. [Dialogue pacing: old]'
    new = update_audio_prompt(old, [("能修的", "能修好的，我们一定修")],
                              [{"start": 0.4, "end": 2.6}, {"start": 3.0, "end": 4.1}])
    assert '"能修好的，我们一定修"' in new and "old" not in new and "No music." in new
    assert new.count("Dialogue pacing:") == 1
    assert "0.4s" in new and "4.1s" in new


# ---------------------------------------------------------------- 说话起点
def test_frames_to_segments_merges_short_gaps_and_drops_blips():
    v = np.zeros(500, dtype=bool)          # 10ms 一帧
    v[82:110] = True                       # 0.82–1.10
    v[130:240] = True                      # 1.30–2.40：和上一段只隔 0.2s，并成一句
    v[305:308] = True                      # 太短，丢掉
    v[360:450] = True
    segs = frames_to_segments(v, 0.01, 0.0, merge_gap=0.25, min_len=0.12)
    assert segs == [(0.82, 2.39), (3.6, 4.49)]


def tones_noise_silence(sr=16000, seed=0):
    """0.5s 静 + 1.0s 200Hz 带谐波的"嗓音" + 0.8s 响白噪声（环境声）+ 0.7s 嗓音 + 0.5s 静。"""
    rng = np.random.default_rng(seed)
    t = lambda d: np.arange(int(d * sr)) / sr  # noqa: E731
    voice = lambda d: 0.2 * (np.sin(2 * np.pi * 200 * t(d)) + 0.5 * np.sin(2 * np.pi * 400 * t(d))  # noqa: E731
                             + 0.3 * np.sin(2 * np.pi * 600 * t(d)))
    parts = [np.zeros(int(0.5 * sr)), voice(1.0), 0.3 * rng.standard_normal(int(0.8 * sr)),
             voice(0.7), np.zeros(int(0.5 * sr))]
    return np.concatenate(parts).astype(np.float32)


def test_speech_segments_find_voiced_tones_and_ignore_loud_noise():
    segs = speech_segments_from_pcm(tones_noise_silence(), 16000, merge_gap=0.25)
    assert len(segs) == 2
    assert segs[0][0] == pytest.approx(0.5, abs=0.05) and segs[0][1] == pytest.approx(1.5, abs=0.05)
    assert segs[1][0] == pytest.approx(2.3, abs=0.05) and segs[1][1] == pytest.approx(3.0, abs=0.05)


def test_align_onsets_picks_nearest_segment_in_order_and_falls_back():
    segs = [(0.82, 2.40), (3.60, 4.50)]
    assert align_onsets([0.4, 3.0], segs) == pytest.approx([0.82, 3.60])
    # 第 2 句离任何一段都超过 max_shift：退回计划时间
    assert align_onsets([0.4, 7.0], segs, max_shift=1.5) == pytest.approx([0.82, 7.0])
    # 两句都最靠近第 1 段：第 2 句不能再用同一段
    assert align_onsets([0.5, 1.0], segs) == pytest.approx([0.82, 1.0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="没有 ffmpeg")
def test_speech_segments_on_synthetic_tones(tmp_path):
    from backend.engines.ffmpeg import FFmpeg
    wav = tmp_path / "tones.wav"
    # 0.5s 静 + 1.0s 音 + 0.8s 响噪声 + 0.7s 音 + 0.5s 静
    graph = ("anullsrc=r=48000:cl=mono:d=0.5[s1];sine=f=300:r=48000:d=1.0[t1];"
             "anoisesrc=r=48000:a=0.3:d=0.8[s2];sine=f=250:r=48000:d=0.7[t2];"
             "anullsrc=r=48000:cl=mono:d=0.5[s3];[s1][t1][s2][t2][s3]concat=n=5:v=0:a=1[a]")
    subprocess.run(["ffmpeg", "-y", "-filter_complex", graph, "-map", "[a]", str(wav)],
                   check=True, capture_output=True)
    segs = FFmpeg().speech_segments(wav)
    assert len(segs) == 2
    assert segs[0][0] == pytest.approx(0.5, abs=0.06) and segs[0][1] == pytest.approx(1.5, abs=0.06)
    assert segs[1][0] == pytest.approx(2.3, abs=0.06) and segs[1][1] == pytest.approx(3.0, abs=0.06)


# ---------------------------------------------------------------- 无原声
def test_dub_mix_cmd_without_bed_mutes_original_track():
    cmd = build_dub_mix_cmd("ffmpeg", "in.mp4", [("a.mp3", 1.5, 2.0)], "out.mp4", bed_db=None,
                            duck_db=-30)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:a]volume=0,aformat=")
    assert "dB" not in graph.split("[bed]")[0]
    assert "[bed][v1]amix=inputs=2:normalize=0:dropout_transition=0:duration=first[a]" in graph


def test_export_options_new_fields_and_tag():
    assert ExportOptions().bed is True and ExportOptions().align == "timeline"
    o = ExportOptions(subtitles="zh+km", dub="km", end_card=True, bed=False, align="detected")
    assert o.tag() == "_zhkm_dubkm_nobed_aligned_card"
    with pytest.raises(ValueError):
        ExportOptions(align="lips")


def test_dub_cues_follow_where_the_dub_plays():
    from backend.post.subtitles import dub_cues
    dlg = [[{"speaker": "陈师傅", "zh": "换？要换吗", "km": "ប្ដូរ?"},
            {"speaker": "陈师傅", "zh": "先让我看看。", "km": "សុំមើលសិន។"}]]
    rows = [{"shot": 0, "index": 0, "offset": 1.9, "seconds": 0.42},
            {"shot": 0, "index": 1, "offset": 2.6, "seconds": 1.2}]
    cues = dub_cues(rows, [10.0], dlg)
    assert [(c.start, c.end) for c in cues] == pytest.approx([(11.9, 12.6), (12.6, 14.1)])
    assert cues[0].zh == "换？要换吗" and cues[1].km == "សុំមើលសិន។"
