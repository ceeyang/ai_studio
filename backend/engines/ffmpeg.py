"""ffmpeg 封装：探测、截尾帧、成片合成。

合成移植自 vglib/core.assemble：xfade 的 offset 相对"已拼接内容的时长"计算并留一帧余量，
否则误差逐段累积，offset 越过末尾会让 xfade 静默截断。改动见计划 Task 9。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Literal

XFADE = {"dissolve": "fade", "white_flash": "fadewhite", "black_dip": "fadeblack",
         "fade_out": "fade"}


class FFmpegError(RuntimeError):
    pass


def parse_transition(raw: str | None) -> tuple[str, float]:
    raw = raw or "hard_cut"
    if raw.startswith("hard_cut"):
        return "fade", 0.05
    name, _, num = raw.rpartition("_")
    try:
        dur = float(num)
    except ValueError:
        name, dur = raw, 0.4
    return XFADE.get(name, "fade"), dur


def build_xfade_graph(durs: list[float], transitions: list[str], fps: int,
                      with_audio: bool) -> tuple[str, float]:
    eps = 1.0 / max(fps, 1)
    filters: list[str] = []
    last_v, last_a = "0:v", "0:a"
    acc = durs[0]
    for i in range(len(durs) - 1):
        kind, dur = parse_transition(transitions[i] if i < len(transitions) else None)
        dur = max(min(dur, acc - 2 * eps, durs[i + 1] - 2 * eps), eps)
        offset = max(acc - dur - eps, 0.0)
        filters.append(f"[{last_v}][{i + 1}:v]xfade=transition={kind}:duration={dur:.3f}"
                       f":offset={offset:.3f}[v{i}]")
        last_v = f"v{i}"
        if with_audio:
            filters.append(f"[{last_a}][{i + 1}:a]acrossfade=d={dur:.3f}[a{i}]")
            last_a = f"a{i}"
        acc = offset + durs[i + 1]
    fade_out = max(acc - 0.8, 0.0)
    filters.append(f"[{last_v}]fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out:.3f}:d=0.8[vout]")
    if with_audio:
        filters.append(f"[{last_a}]afade=t=out:st={fade_out:.3f}:d=0.8[aout]")
    return ";".join(filters), acc


def build_normalize_cmd(ffmpeg: str, src, dst, *, width: int, height: int, fps: int,
                        duration: float, audio: Literal["keep", "silent", "none"]) -> list[str]:
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},fps={fps},setsar=1")
    cmd = [ffmpeg, "-y", "-i", str(src)]
    if audio == "silent":
        cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-t", f"{duration:.3f}", "-vf", vf, "-map", "0:v:0"]
    if audio == "keep":
        cmd += ["-map", "0:a:0", "-af", "apad"]
    elif audio == "silent":
        cmd += ["-map", "1:a:0"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
    if audio == "none":
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k"]
    return cmd + [str(dst)]


class FFmpeg:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def run(self, args: list[str]) -> str:
        try:
            p = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            raise FFmpegError(f"找不到 {args[0]}：{e}") from e
        if p.returncode != 0:
            raise FFmpegError(f"{Path(str(args[0])).name} 失败：\n{p.stderr[-1500:]}")
        return p.stdout

    def version(self) -> str:
        return self.run([self.ffmpeg, "-version"])

    def video_duration(self, path: Path) -> float:
        """视频流的真实时长。不能用容器时长：音轨常比画面长几十毫秒，会让 xfade 越界。"""
        out = self.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=duration,nb_frames,r_frame_rate:format=duration", "-of", "json",
                        str(path)])
        d = json.loads(out or "{}")
        st = (d.get("streams") or [{}])[0]
        try:
            nb = int(st.get("nb_frames"))
            num, den = str(st.get("r_frame_rate", "0/1")).split("/")
            fps = float(num) / float(den)
            if nb > 0 and fps > 0:
                return nb / fps
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        for v in (st.get("duration"), (d.get("format") or {}).get("duration")):
            try:
                if float(v) > 0:
                    return float(v)
            except (TypeError, ValueError):
                pass
        raise FFmpegError(f"读不出视频时长：{path}")

    def has_audio(self, path: Path) -> bool:
        out = self.run([self.ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=codec_name", "-of", "csv=p=0", str(path)])
        return bool(out.strip())

    def last_frame(self, video: Path, out_png: Path) -> Path:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-sseof", "-1", "-i", str(video), "-update", "1",
                  "-q:v", "2", str(out_png)])
        if not out_png.exists():
            raise FFmpegError(f"没截到尾帧：{video}")
        return out_png

    def assemble(self, clips: list[Path], out: Path, *, width: int, height: int, fps: int,
                 transitions: list[str], work_dir: Path) -> Path:
        if not clips:
            raise FFmpegError("没有片段可合成")
        out, work_dir = Path(out), Path(work_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        flags = [self.has_audio(c) for c in clips]
        any_audio = any(flags)
        norm: list[Path] = []
        durs: list[float] = []
        for i, (clip, has) in enumerate(zip(clips, flags)):
            dst = work_dir / f"n{i:03d}.mp4"
            mode = "keep" if has else ("silent" if any_audio else "none")
            self.run(build_normalize_cmd(self.ffmpeg, clip, dst, width=width, height=height,
                                         fps=fps, duration=self.video_duration(clip), audio=mode))
            norm.append(dst)
            durs.append(self.video_duration(dst))
        if len(norm) == 1:
            shutil.copyfile(norm[0], out)
            return out
        graph, _ = build_xfade_graph(durs, transitions, fps, any_audio)
        cmd = [self.ffmpeg, "-y"]
        for p in norm:
            cmd += ["-i", str(p)]
        cmd += ["-filter_complex", graph, "-map", "[vout]"]
        if any_audio:
            cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", str(fps), "-movflags", "+faststart", str(out)]
        self.run(cmd)
        return out
