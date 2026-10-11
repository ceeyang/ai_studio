import json
import shutil
import subprocess
import sys
import threading
import time

import pytest

from backend.engines.ffmpeg import (FFmpeg, FFmpegCanceled, FFmpegError, build_normalize_cmd,
                                    build_xfade_graph, hard_cuts, parse_scene_cuts,
                                    parse_transition, xfade_timeline)
from backend.queue.errors import JobCanceled

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="没有 ffmpeg")


def test_parse_transition():
    assert parse_transition("hard_cut") == ("fade", 0.05)
    assert parse_transition(None) == ("fade", 0.05)
    assert parse_transition("dissolve_0.5") == ("fade", 0.5)
    assert parse_transition("white_flash_0.2") == ("fadewhite", 0.2)
    assert parse_transition("black_dip_0.3") == ("fadeblack", 0.3)
    assert parse_transition("dissolve") == ("fade", 0.4)


def test_xfade_offsets_and_audio():
    graph, total = build_xfade_graph([5.0, 5.0, 5.0], ["dissolve_0.5", "hard_cut"], 24,
                                     with_audio=True)
    assert "[0:v][1:v]xfade=transition=fade:duration=0.500:offset=4.458[v0]" in graph
    assert "[v0][2:v]xfade=transition=fade:duration=0.050:offset=9.367[v1]" in graph
    assert "[0:a][1:a]acrossfade=d=0.500[a0]" in graph
    assert "[vout]" in graph and "[aout]" in graph
    assert abs(total - 14.3667) < 1e-3


def test_xfade_timeline_starts_match_graph_offsets():
    joins, starts, total = xfade_timeline([5.0, 5.0, 5.0], ["dissolve_0.5", "hard_cut"], 24)
    assert [round(j[2], 3) for j in joins] == [4.458, 9.367]
    assert starts == [0.0, joins[0][2], joins[1][2]]
    assert abs(total - 14.3667) < 1e-3
    assert xfade_timeline([2.0], [], 24) == ([], [0.0], 2.0)


@needs_ffmpeg
def test_assemble_trims_clips_to_max_seconds(tmp_path):
    ff = FFmpeg()
    clips = [make_clip(tmp_path / "a.mp4", 2.0, True), make_clip(tmp_path / "b.mp4", 2.0, True)]
    res = ff.assemble(clips, tmp_path / "o.mp4", width=270, height=480, fps=24,
                      transitions=["hard_cut"], work_dir=tmp_path / "w", max_seconds=[1.5, None])
    assert abs(res.durations[0] - 1.5) < 0.05 and abs(res.durations[1] - 2.0) < 0.05
    assert abs(res.starts[1] - (res.durations[0] - 0.05 - 1 / 24)) < 1e-6
    assert abs(ff.video_duration(res.path) - res.total) < 0.1


def test_xfade_without_audio():
    graph, _ = build_xfade_graph([3.0, 3.0], ["hard_cut"], 16, with_audio=False)
    assert "acrossfade" not in graph and "[aout]" not in graph


def test_normalize_cmd_modes():
    kw = dict(width=1080, height=1920, fps=24, duration=5.0)
    keep = build_normalize_cmd("ffmpeg", "a.mp4", "n.mp4", audio="keep", **kw)
    assert "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=24,setsar=1" in keep
    assert "0:a:0" in keep and "apad" in keep and "-an" not in keep
    silent = build_normalize_cmd("ffmpeg", "a.mp4", "n.mp4", audio="silent", **kw)
    assert "anullsrc=r=48000:cl=stereo" in silent and "1:a:0" in silent
    none = build_normalize_cmd("ffmpeg", "a.mp4", "n.mp4", audio="none", **kw)
    assert "-an" in none


def test_assemble_empty_raises(tmp_path):
    with pytest.raises(FFmpegError):
        FFmpeg().assemble([], tmp_path / "o.mp4", width=270, height=480, fps=24,
                          transitions=[], work_dir=tmp_path / "w")


def make_clip(path, seconds, audio):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=24:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_assemble_mixed_audio_keeps_sound(tmp_path):
    ff = FFmpeg()
    d = tmp_path / "素材 目录"
    d.mkdir()
    clips = [make_clip(d / "一.mp4", 1.5, True), make_clip(d / "二.mp4", 1.5, False),
             make_clip(d / "三.mp4", 1.5, True)]
    out = tmp_path / "成片" / "第1集.mp4"
    ff.assemble(clips, out, width=270, height=480, fps=24,
                transitions=["dissolve_0.5", "hard_cut"], work_dir=tmp_path / "work")
    assert out.exists() and ff.has_audio(out)
    v = ff.video_duration(out)
    assert 3.6 < v < 4.2  # 4.5 - 0.5 - 0.05 - 两帧余量 ≈ 3.87
    info = json.loads(ff.run([ff.ffprobe, "-v", "error", "-select_streams", "a:0",
                              "-show_entries", "stream=duration", "-of", "json", str(out)]))
    assert abs(float(info["streams"][0]["duration"]) - v) < 0.15


@needs_ffmpeg
def test_single_clip_and_last_frame(tmp_path):
    ff = FFmpeg()
    clip = make_clip(tmp_path / "a.mp4", 1.0, False)
    res = ff.assemble([clip], tmp_path / "o.mp4", width=270, height=480, fps=24,
                      transitions=[], work_dir=tmp_path / "w")
    out = res.path
    assert res.starts == [0.0] and abs(res.total - 1.0) < 0.05
    assert out.exists() and not ff.has_audio(out)
    png = ff.last_frame(clip, tmp_path / "尾帧.png")
    assert png.stat().st_size > 0


@needs_ffmpeg
def test_sound_end_finds_trailing_silence(tmp_path):
    ff = FFmpeg()
    clip = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=24:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-af", "apad",
                    "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(clip)],
                   check=True, capture_output=True)
    assert abs(ff.sound_end(clip) - 1.2) < 0.1
    silent = make_clip(tmp_path / "b.mp4", 1.0, False)
    assert ff.sound_end(silent) is None


@needs_ffmpeg
def test_trim_silence_removes_tts_padding(tmp_path):
    ff = FFmpeg()
    raw = tmp_path / "raw.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=0.2",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=0.4",
                    "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1.4",
                    "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", str(raw)],
                   check=True, capture_output=True)
    assert ff.duration(raw) > 1.9
    out = ff.trim_silence(raw, tmp_path / "line.wav")
    assert abs(ff.duration(out) - 0.4) < 0.08


# ---------------------------------------------------------------- 取消
LONG = [sys.executable, "-c", "import time; time.sleep(30)"]


def test_run_with_cancel_kills_long_subprocess():
    """导出时取消：正在跑的 ffmpeg（这里用一个睡 30 秒的假进程）要被杀掉，抛 JobCanceled。"""
    ev = threading.Event()
    ff = FFmpeg().with_cancel(ev)
    threading.Timer(0.3, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(FFmpegCanceled) as info:
        ff.run(LONG)
    assert isinstance(info.value, JobCanceled)
    assert time.monotonic() - t0 < 5


def test_run_already_canceled_does_not_start():
    ev = threading.Event()
    ev.set()
    with pytest.raises(FFmpegCanceled):
        FFmpeg().with_cancel(ev).run([sys.executable, "-c", "raise SystemExit(3)"])


def test_run_with_cancel_still_returns_output_and_errors():
    ff = FFmpeg().with_cancel(threading.Event())
    assert ff.run([sys.executable, "-c", "print('ok')"]).strip() == "ok"
    with pytest.raises(FFmpegError, match="boom"):
        ff.run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"])
    with pytest.raises(FFmpegError, match="找不到"):
        ff.run(["definitely-not-a-real-binary-xyz"])


def test_with_cancel_keeps_paths_and_shared_instance_unbound():
    base = FFmpeg("ff", "fp")
    bound = base.with_cancel(threading.Event())
    assert (bound.ffmpeg, bound.ffprobe) == ("ff", "fp")
    assert base.cancel is None and bound is not base


# ---------------------------------------------------------------- 出片自检：片段内部硬切
SHOWINFO_LOG = """\
[Parsed_showinfo_1 @ 0000019fc0cf4d80] config in time_base: 1/12288, frame_rate: 24/1
[Parsed_showinfo_1 @ 0000019fc0cf4d80] n:   0 pts:  12288 pts_time:1       duration:    512 \
duration_time:0.0416667 fmt:yuv420p
[Parsed_showinfo_1 @ 0000019fc0cf4d80] n:   1 pts:  30720 pts_time:2.5     duration:    512
[Parsed_showinfo_1 @ 0000019fc0cf4d80] n:   2 pts:  49000 pts_time:3.98    duration:    512
[out#0/null @ 000001] video:1KiB audio:0KiB pts_time:9.9
"""


def test_parse_scene_cuts_and_edges():
    assert parse_scene_cuts(SHOWINFO_LOG) == [1.0, 2.5, 3.98]
    assert parse_scene_cuts("") == []
    # 贴着结尾（4 秒片段的 3.98 秒）和开头的突变不算硬切
    assert hard_cuts([0.0, 1.0, 2.5, 3.98], 4.0) == [1.0, 2.5]
    assert hard_cuts([0.05, 1.0], None) == [1.0]


@needs_ffmpeg
def test_scene_cuts_finds_hard_cut_in_synthetic_clip(tmp_path):
    """前 1 秒 testsrc、后 1 秒彩条拼成一条：相当于 H3 中途跳到定妆照。"""
    cut = tmp_path / "cut.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=24:duration=1",
                    "-f", "lavfi", "-i", "smptebars=size=160x120:rate=24:duration=1",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(cut)], check=True, capture_output=True)
    ff = FFmpeg()
    cuts = ff.scene_cuts(cut)
    assert len(cuts) == 1 and abs(cuts[0] - 1.0) < 0.1
    smooth = make_clip(tmp_path / "smooth.mp4", 2.0, False)
    assert ff.scene_cuts(smooth) == []


def test_scene_cuts_bad_file_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"x")
    with pytest.raises(FFmpegError):
        FFmpeg().scene_cuts(bad)


# ---------------------------------------------------------------- 第三次样片：视频开头没接上首帧
def test_parse_ssim():
    from backend.engines.ffmpeg import parse_ssim

    log = ("[Parsed_ssim_4 @ 000001] SSIM Y:0.912345 (10.586) All:0.912345 (10.586)\n"
           "[out#0/null @ 0001] video:1KiB")
    assert parse_ssim(log) == pytest.approx(0.912345)
    assert parse_ssim("nothing") is None


@needs_ffmpeg
def test_first_frame_ssim_matches_and_mismatches(tmp_path):
    """视频开头就是首帧（testsrc 第 0 帧，尺寸不同也能比）→ 高；首帧是彩条（H3 重新构图）→ 低。"""
    clip = make_clip(tmp_path / "c.mp4", 1.0, False)
    same = tmp_path / "same.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=24:duration=1",
                    "-frames:v", "1", str(same)], check=True, capture_output=True)
    other = tmp_path / "other.png"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "smptebars=size=640x480:rate=24:duration=1",
                    "-frames:v", "1", str(other)], check=True, capture_output=True)
    ff = FFmpeg()
    hi = ff.first_frame_ssim(clip, same)
    lo = ff.first_frame_ssim(clip, other)
    assert hi > 0.8 and lo < 0.6, (hi, lo)


def test_first_frame_ssim_bad_file_raises(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"x")
    with pytest.raises(FFmpegError):
        FFmpeg().first_frame_ssim(bad, bad)
