"""后期纯函数：裁尾规则、字幕时间轴与 ASS、片尾卡滤镜、配音语速与混音。不跑 ffmpeg。"""
import pytest

from backend.engines.ffmpeg import parse_sound_end, xfade_timeline
from backend.post.dub import build_dub_mix_cmd, fit_rate, plan_dub_lines
from backend.post.render import CardSpec, build_finish_cmd
from backend.post.subtitles import (ass_time, build_endcard_ass, build_subtitles_ass, timeline_cues,
                                    wrap_zh)
from backend.post.timeline import trim_limit


# ---------------------------------------------------------------- 裁尾
def test_trim_cuts_tail_when_dialogue_ends_before_cut():
    # 4.5s 的镜头 H3 出了 5.17s，台词 3.2s 就说完：裁到 4.65s
    assert trim_limit(5.167, 4.5, [{"start": 0.5, "end": 3.2}]) == pytest.approx(4.65)


def test_trim_keeps_full_clip_when_dialogue_runs_past_cut():
    # 最后一句到 4.5s，加 0.3s 收尾就越过 4.65s：整段保留，宁可长也不切台词
    assert trim_limit(5.167, 4.5, [{"start": 0.5, "end": 4.5}]) is None


def test_trim_respects_measured_speech_end():
    # 分镜说台词 3.2s 结束，但 H3 实际说到 4.8s（实测 S01）：不能裁
    assert trim_limit(5.167, 4.5, [{"start": 0.5, "end": 3.2}], audio_end=4.8) is None
    # 实测声音 4.2s 就停了：照常裁
    assert trim_limit(5.167, 4.5, [{"start": 0.5, "end": 3.2}], audio_end=4.2) == pytest.approx(4.65)


def test_parse_sound_end():
    tail = ("[silencedetect @ 0x1] silence_start: 0.2\n"
            "[silencedetect @ 0x1] silence_end: 0.9 | silence_duration: 0.7\n"
            "[silencedetect @ 0x1] silence_start: 4.31\n")
    assert parse_sound_end(tail, 5.0) == pytest.approx(4.31)
    closed = ("[silencedetect @ 0x1] silence_start: 1.0\n"
              "[silencedetect @ 0x1] silence_end: 2.0 | silence_duration: 1\n")
    assert parse_sound_end(closed, 5.0) == 5.0           # 一直响到结尾
    assert parse_sound_end("", 5.0) == 5.0
    flushed = closed + ("[silencedetect @ 0x1] silence_start: 3.2\n"
                        "[silencedetect @ 0x1] silence_end: 5.01 | x\n")
    assert parse_sound_end(flushed, 5.0) == pytest.approx(3.2)   # ffmpeg 8 在结尾补 silence_end
    assert parse_sound_end("[silencedetect @ 0x1] silence_start: 0\n", 5.0) == 0.0   # 全程静音


def test_trim_noop_when_clip_not_longer_than_cut():
    assert trim_limit(5.167, 5.0, []) is None          # 5.167 ≤ 5.15 + 一帧
    assert trim_limit(4.0, 5.0, []) is None
    assert trim_limit(6.583, 6.0, [], tolerance=0.0) == pytest.approx(6.0)


# ---------------------------------------------------------------- 字幕
def test_ass_time_format():
    assert ass_time(0) == "0:00:00.00"
    assert ass_time(61.257) == "0:01:01.26"
    assert ass_time(-1) == "0:00:00.00"


def test_wrap_zh_breaks_after_punctuation():
    assert wrap_zh("短句", 10) == "短句"
    assert wrap_zh("修车、改装、钣金喷漆，保险理赔、买车过户", 12) == "修车、改装、钣金喷漆，\\N保险理赔、买车过户"


def test_timeline_cues_follow_xfade_offsets():
    durs = [4.65, 5.167]
    _, starts, _ = xfade_timeline(durs, ["hard_cut"], 24)
    shots = [[{"speaker": "小雨", "zh": "师傅，别人都说这个得换。", "km": "ជាង!", "start": 0.5, "end": 3.2}],
             [{"speaker": "陈师傅", "zh": "换？", "km": "ដូរ?", "start": 2.0, "end": 2.6},
              {"speaker": "陈师傅", "zh": "先让我看看。", "km": "", "start": 2.8, "end": 9.0}]]
    cues = timeline_cues(shots, starts, durs)
    second = 4.65 - 0.05 - 1 / 24
    assert starts[1] == pytest.approx(second)
    assert [(round(c.start, 3), round(c.end, 3)) for c in cues] == [
        (0.5, 3.2), (round(second + 2.0, 3), round(second + 2.6, 3)),
        (round(second + 2.8, 3), round(second + 5.167, 3))]   # 超出片段的 end 截到片段结尾
    assert cues[1].km == "ដូរ?" and cues[2].km == ""


def test_timeline_cues_do_not_overlap():
    shots = [[{"zh": "一", "start": 0.4, "end": 2.0}, {"zh": "二", "start": 1.8, "end": 3.0}]]
    a, b = timeline_cues(shots, [0.0], [5.0])
    assert a.end <= b.start


def test_subtitles_ass_two_lines_zh_on_top_km_below():
    shots = [[{"zh": "能修的，我们绝对不换。", "km": "អ្វីដែលជួសជុលបាន យើងមិនដូរឡើយ។", "start": 1.4, "end": 3.8}]]
    cues = timeline_cues(shots, [10.0], [5.0])
    ass = build_subtitles_ass(cues, 1080, 1920, mode="zh+km", zh_font="Microsoft YaHei",
                              km_font="Khmer UI")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Style: ZH,Microsoft YaHei," in ass and "Style: KM,Khmer UI," in ass
    ev = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert ev == ["Dialogue: 0,0:00:11.40,0:00:13.80,ZH,,0,0,0,,"
                  "能修的，我们绝对不换。\\N{\\rKM}អ្វីដែលជួសជុលបាន យើងមិនដូរឡើយ។"]


def test_subtitles_ass_zh_only_and_skips_empty():
    shots = [[{"zh": "能修的。", "km": "បាន", "start": 0.5, "end": 1.5}, {"zh": "", "km": "", "start": 2, "end": 3}]]
    ass = build_subtitles_ass(timeline_cues(shots, [0.0], [5.0]), 1080, 1920, mode="zh",
                              zh_font="Microsoft YaHei", km_font="Khmer UI")
    ev = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert ev == ["Dialogue: 0,0:00:00.50,0:00:01.50,ZH,,0,0,0,,能修的。"]


def test_subtitles_ass_rejects_unknown_mode():
    with pytest.raises(ValueError):
        build_subtitles_ass([], 1080, 1920, mode="km", zh_font="a", km_font="b")


# ---------------------------------------------------------------- 片尾卡
CARD = {"seconds": 3, "lines": [
    {"text": "កម្ពុជារថយន្តលេខរៀងទី1", "font": "Khmer UI", "size": 90, "bold": True},
    {"text": "柬埔寨1号铭车", "font": "Microsoft YaHei", "size": 80, "bold": True},
    {"text": "", "size": 30},
    {"text": "Telegram 0318891666", "size": 60}]}


def test_endcard_ass_centers_lines_with_fonts():
    ass = build_endcard_ass(CARD["lines"], 1080, 1920, 3.0, default_font="Arial")
    ev = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
    assert len(ev) == 3   # 空行只占高度
    assert ev[0].startswith("Dialogue: 0,0:00:00.00,0:00:03.00,CARD,,0,0,0,,{\\an5\\pos(540,")
    assert "\\fnKhmer UI\\fs90\\b1" in ev[0] and ev[0].endswith("}កម្ពុជារថយន្តលេខរៀងទី1")
    assert "\\fnArial\\fs60\\b0" in ev[2]                     # 没写字体用默认
    ys = [int(e.split("\\pos(540,")[1].split(")")[0]) for e in ev]
    assert ys == sorted(ys)
    total = sum(int(l["size"] * 1.35) for l in CARD["lines"])
    assert abs((ys[0] - 45) - (1920 - total) // 2) <= 1          # 整块垂直居中


def test_finish_cmd_with_subtitles_and_end_card():
    card = CardSpec(ass="card.ass", seconds=3.0, width=1080, height=1920, fps=24,
                    color="0x0b1a33", fade=0.5)
    cmd = build_finish_cmd("ffmpeg", "in.mp4", "out.mp4", subs_ass="subs.ass", card=card,
                           has_audio=True, fonts_dir="_fonts")
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "in.mp4"]
    assert "color=c=0x0b1a33:s=1080x1920:r=24:d=3.000" in cmd
    assert "anullsrc=r=48000:cl=stereo" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v]ass=subs.ass:fontsdir=_fonts,setsar=1,format=yuv420p[mv]" in graph
    assert "[1:v]ass=card.ass:fontsdir=_fonts,fade=t=in:st=0:d=0.500,setsar=1,format=yuv420p[cv]" in graph
    assert "[mv][ma][cv][ca]concat=n=2:v=1:a=1[v][a]" in graph
    assert cmd[cmd.index("-map") + 1] == "[v]" and "[a]" in cmd and cmd[-1] == "out.mp4"


def test_finish_cmd_card_only_without_audio():
    card = CardSpec(ass="card.ass", seconds=2.0, width=270, height=480, fps=24)
    cmd = build_finish_cmd("ffmpeg", "in.mp4", "out.mp4", subs_ass=None, card=card,
                           has_audio=False, fonts_dir="_fonts")
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v]setsar=1,format=yuv420p[mv]" in graph and "ass=subs" not in graph
    assert "[mv][cv]concat=n=2:v=1:a=0[v]" in graph and "anullsrc" not in " ".join(cmd)


def test_finish_cmd_subtitles_only_copies_audio():
    cmd = build_finish_cmd("ffmpeg", "in.mp4", "out.mp4", subs_ass="subs.ass", card=None,
                           has_audio=True, fonts_dir="_fonts")
    assert cmd[cmd.index("-vf") + 1] == "ass=subs.ass:fontsdir=_fonts"
    assert cmd[cmd.index("-c:a") + 1] == "copy"


# ---------------------------------------------------------------- 配音
def test_fit_rate():
    assert fit_rate(2.0, 3.0, 35) == 0            # 放得下就原速
    assert fit_rate(3.3, 3.0, 35) == 12           # 超 10% → +10% 再加 2 个点余量
    assert fit_rate(6.0, 3.0, 35) == 35           # 封顶
    assert fit_rate(2.0, 0.0, 35) == 0            # 没有窗口不提速


def test_plan_dub_lines_windows_follow_timeline():
    shots = [[{"speaker": "陈师傅", "km": "ដូរ?", "start": 2.0, "end": 2.6},
              {"speaker": "陈师傅", "km": "សុំមើលសិន។", "start": 2.8, "end": 4.6},
              {"speaker": "陈师傅", "km": "", "start": 4.7, "end": 4.9}],
             [{"speaker": "小雨", "km": "បាទ", "start": 0.4, "end": 1.0}]]
    lines = plan_dub_lines(shots, [0.0, 4.9], [5.0, 5.0], gap=0.1)
    assert [(l.shot, l.index, l.speaker, l.text) for l in lines] == [
        (0, 0, "陈师傅", "ដូរ?"), (0, 1, "陈师傅", "សុំមើលសិន។"), (1, 0, "小雨", "បាទ")]
    assert lines[0].start == pytest.approx(2.0) and lines[0].window == pytest.approx(0.7)
    assert lines[1].window == pytest.approx(5.0 - 2.8 - 0.1)   # 到本镜结尾
    assert lines[2].start == pytest.approx(5.3)


def test_dub_mix_cmd_ducks_bed_and_delays_lines():
    cmd = build_dub_mix_cmd("ffmpeg", "in.mp4", [("a.mp3", 1.5, 2.0), ("b.mp3", 4.0, 1.0)],
                            "out.mp4", bed_db=-14, duck_db=-30)
    assert cmd[cmd.index("-i") + 1] == "in.mp4" and "a.mp3" in cmd and "b.mp3" in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.startswith("[0:a]volume=-14.0dB,volume=-16.0dB:enable='between(t,1.35,3.75)+between(t,3.85,5.25)'")
    assert "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,adelay=1500:all=1[v1]" in graph
    assert "adelay=4000:all=1[v2]" in graph
    assert "[bed][v1][v2]amix=inputs=3:normalize=0:dropout_transition=0:duration=first[a]" in graph
    assert cmd[cmd.index("-c:v") + 1] == "copy"
