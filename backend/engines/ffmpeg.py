"""ffmpeg 封装：探测、截尾帧、成片合成。

合成移植自 vglib/core.assemble：xfade 的 offset 相对"已拼接内容的时长"计算并留一帧余量，
否则误差逐段累积，offset 越过末尾会让 xfade 静默截断。改动见计划 Task 9。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.queue.errors import JobCanceled

XFADE = {"dissolve": "fade", "white_flash": "fadewhite", "black_dip": "fadeblack",
         "fade_out": "fade"}


class FFmpegError(RuntimeError):
    pass


class FFmpegCanceled(JobCanceled):
    """任务被取消：正在跑的 ffmpeg 已被终止。是 JobCanceled，队列会把任务记成「已取消」。"""


@dataclass
class Assembled:
    """合成结果：durations 是每镜实际用了多少秒（裁过尾），starts 是每镜在成片里的起点。"""
    path: Path
    durations: list[float]
    starts: list[float]
    total: float


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


def xfade_timeline(durs: list[float], transitions: list[str], fps: int
                   ) -> tuple[list[tuple[str, float, float]], list[float], float]:
    """xfade 拼接的时间轴：每个接缝的 (转场, 时长, offset)、每镜在成片里的起点、成片总长。

    字幕和配音的时间也按它算，必须和 build_xfade_graph 用同一套数。
    """
    eps = 1.0 / max(fps, 1)
    joins: list[tuple[str, float, float]] = []
    starts = [0.0]
    acc = durs[0] if durs else 0.0
    for i in range(len(durs) - 1):
        kind, dur = parse_transition(transitions[i] if i < len(transitions) else None)
        dur = max(min(dur, acc - 2 * eps, durs[i + 1] - 2 * eps), eps)
        offset = max(acc - dur - eps, 0.0)
        joins.append((kind, dur, offset))
        starts.append(offset)
        acc = offset + durs[i + 1]
    return joins, starts, acc


def build_xfade_graph(durs: list[float], transitions: list[str], fps: int,
                      with_audio: bool) -> tuple[str, float]:
    filters: list[str] = []
    last_v, last_a = "0:v", "0:a"
    joins, _, acc = xfade_timeline(durs, transitions, fps)
    for i, (kind, dur, offset) in enumerate(joins):
        filters.append(f"[{last_v}][{i + 1}:v]xfade=transition={kind}:duration={dur:.3f}"
                       f":offset={offset:.3f}[v{i}]")
        last_v = f"v{i}"
        if with_audio:
            filters.append(f"[{last_a}][{i + 1}:a]acrossfade=d={dur:.3f}[a{i}]")
            last_a = f"a{i}"
    fade_out = max(acc - 0.8, 0.0)
    filters.append(f"[{last_v}]fade=t=in:st=0:d=0.6,fade=t=out:st={fade_out:.3f}:d=0.8[vout]")
    if with_audio:
        filters.append(f"[{last_a}]afade=t=out:st={fade_out:.3f}:d=0.8[aout]")
    return ";".join(filters), acc


SCENE_THRESHOLD = 0.4
CUT_EDGE = 0.15      # 开头/结尾这么近的突变不算（编码首帧、淡出）
_SHOWINFO_PTS = re.compile(r"Parsed_showinfo.*?\bpts_time:\s*(-?[\d.]+)")


SSIM_SIZE = (32, 32)
_SSIM_LINE = re.compile(r"SSIM\s+(Y:.*)")
# SSIM 可能是负数（两张图结构相反，如暗场对亮灰底）：以前只认非负数，Y 是负的时被漏掉、只剩 U / V
_SSIM_CH = re.compile(r"\b([YUV]):\s*(-?[0-9.]+)")
_SSIM_ALL = re.compile(r"All:\s*(-?[0-9.]+)")


def parse_ssim(log: str) -> float | None:
    """ffmpeg ssim 滤镜的汇总行「SSIM Y:… U:… V:… All:… (…)」：取 Y / U / V 里最低的一个
    （只看亮度时纯红和纯绿几乎一样；颜色变了 U / V 会掉下来）。没有分通道时退回 All。"""
    lines = _SSIM_LINE.findall(log or "")
    if not lines:
        return None
    try:
        chans = [float(v) for _, v in _SSIM_CH.findall(lines[-1])]
        if chans:
            return min(chans)
        m = _SSIM_ALL.search(lines[-1])
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def parse_scene_cuts(showinfo_log: str) -> list[float]:
    """select='gt(scene,X)',showinfo 的 stderr → 画面突变的时间点（秒）。"""
    return [float(x) for x in _SHOWINFO_PTS.findall(showinfo_log or "")]


def hard_cuts(times: list[float], duration: float | None, edge: float = CUT_EDGE) -> list[float]:
    """片段内部的硬切：去掉贴着开头、结尾的突变。"""
    end = duration if duration and duration > 0 else None
    return [round(t, 2) for t in times if t > edge and (end is None or t < end - edge)]


SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def parse_sound_end(silencedetect_log: str, duration: float) -> float:
    """silencedetect 的输出 → 最后一段声音在哪里结束。

    结尾那段静音的起点就是答案：老版本 ffmpeg 不给它 silence_end，8.x 在流结束时补一个 ≈ 时长的 silence_end。
    """
    starts = [float(x) for x in SILENCE_START.findall(silencedetect_log)]
    ends = [float(x) for x in SILENCE_END.findall(silencedetect_log)]
    if not starts:
        return duration
    if len(starts) > len(ends) or ends[-1] >= duration - 0.1:
        return max(starts[-1], 0.0)
    return duration


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


POLL_SECONDS = 0.2


class FFmpeg:
    """cancel 不为空时，每条命令跑的过程中都看它：一旦置位就杀掉 ffmpeg 并抛 FFmpegCanceled。
    处理器用 with_cancel(ctx.cancel_event) 拿一个绑定了本任务取消信号的副本（共享实例不带）。"""

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 cancel: threading.Event | None = None):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.cancel = cancel

    def with_cancel(self, cancel: threading.Event | None) -> "FFmpeg":
        return type(self)(self.ffmpeg, self.ffprobe, cancel)

    def check_canceled(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise FFmpegCanceled()

    def _exec(self, args: list, *, cwd: Path | None = None, text: bool = True
              ) -> subprocess.CompletedProcess:
        """跑一条命令并收集输出；绑定了取消信号时轮询它，取消就 kill 子进程。"""
        args = [str(a) for a in args]
        kw = dict(encoding="utf-8", errors="replace") if text else {}
        self.check_canceled()
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, cwd=cwd, text=text, **kw)
        except FileNotFoundError as e:
            raise FFmpegError(f"找不到 {args[0]}：{e}") from e
        while True:
            try:
                out, err = proc.communicate(timeout=None if self.cancel is None else POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if self.cancel is not None and self.cancel.is_set():
                    proc.kill()
                    proc.communicate()
                    raise FFmpegCanceled() from None
        return subprocess.CompletedProcess(args, proc.returncode, out, err)

    def run(self, args: list[str], cwd: Path | None = None) -> str:
        p = self._exec(args, cwd=cwd)
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

    def sound_end(self, path: Path, *, noise_db: float = -30.0, min_silence: float = 0.25
                  ) -> float | None:
        """音轨里最后一段声音（台词、动作声）在第几秒结束；低于 noise_db 当作环境底噪。没有音轨返回 None。"""
        if not self.has_audio(path):
            return None
        p = self._exec([self.ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
                        "-f", "null", "-"])
        if p.returncode != 0:
            raise FFmpegError(f"silencedetect 失败：\n{p.stderr[-1500:]}")
        return parse_sound_end(p.stderr, self.duration(path))

    def speech_segments(self, path: Path, *, min_db: float = -45.0, periodicity: float = 0.5,
                        limit: float | None = None, merge_gap: float = 0.25,
                        min_len: float = 0.12) -> list[tuple[float, float]]:
        """音轨里"在说话"的段落（秒）：带通到人声频段后按帧找浊音（能量 + 周期性），见 post/speech.py。
        没有音轨返回 []。"""
        import numpy as np

        from backend.post.speech import SAMPLE_RATE, build_pcm_cmd, speech_segments_from_pcm
        if not self.has_audio(path):
            return []
        p = self._exec(build_pcm_cmd(self.ffmpeg, path, limit=limit), text=False)
        if p.returncode != 0:
            raise FFmpegError(f"解音轨失败：\n{p.stderr.decode('utf-8', 'replace')[-1500:]}")
        x = np.frombuffer(p.stdout, dtype=np.float32)
        return speech_segments_from_pcm(x, SAMPLE_RATE, min_db=min_db, periodicity=periodicity,
                                        merge_gap=merge_gap, min_len=min_len)

    def voice_pitch(self, path: Path, windows: list[tuple[float, float]]) -> float | None:
        """音轨在这些时间窗（秒）里人声基频的中位数（Hz），见 post/speech.median_pitch。
        没有音轨、浊音太少（没人声、太小声）返回 None。"""
        import numpy as np

        from backend.post.speech import SAMPLE_RATE, build_pcm_cmd, median_pitch
        if not self.has_audio(path):
            return None
        p = self._exec(build_pcm_cmd(self.ffmpeg, path), text=False)
        if p.returncode != 0:
            raise FFmpegError(f"解音轨失败：\n{p.stderr.decode('utf-8', 'replace')[-1500:]}")
        x = np.frombuffer(p.stdout, dtype=np.float32)
        return median_pitch(x, SAMPLE_RATE, list(windows))

    def scene_cuts(self, path: Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
        """视频片段内部的硬切（场景突变）时间点。用来查生成视频中途跳到参考图、换场景这种静默失败。"""
        p = self._exec([self.ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an",
                        "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
        if p.returncode != 0:
            raise FFmpegError(f"场景检测失败：\n{(p.stderr or '')[-1500:]}")
        try:
            dur = self.video_duration(path)
        except FFmpegError:
            dur = None
        return hard_cuts(parse_scene_cuts(p.stderr), dur)

    def first_frame_ssim(self, video: Path, image: Path) -> float:
        """视频第 0 帧和给的首帧图有多像（SSIM，0–1，取 Y / U / V 里最低的）。两张都缩到 SSIM_SIZE 再比，
        分辨率、画幅不同也能比。缩得很小是故意的：只看构图，轻微推拉、平移和编码噪声不算。
        结果再和平均颜色的相似度取较小的（见 _mean_color_similarity）。
        第三次样片实测（32×32、yuv444p、取 Y/U/V 最低、再和平均颜色取小）：正常 0.97–0.99，开头轻微放大平移 0.76，
        H3 没从首帧开始（重新构图）0.13–0.20；阈值 0.6 见 pipeline/videos.START_SSIM_MIN。"""
        w, h = SSIM_SIZE
        # 两张直接缩成同样大小再比：假定首帧图和视频画幅一致（视频就是从这张首帧按项目画幅出的）；
        # 画幅不一致时两边一起被拉伸，构图仍能比，只是数值偏低。yuv444p：色度不降采样，U / V 也能比
        graph = (f"[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={w}:{h},format=yuv444p[a];"
                 f"[1:v]setpts=PTS-STARTPTS,scale={w}:{h},format=yuv444p[b];[a][b]ssim")
        p = self._exec([self.ffmpeg, "-hide_banner", "-nostats", "-i", str(video), "-i", str(image),
                        "-an", "-filter_complex", graph, "-frames:v", "1", "-f", "null", "-"])
        if p.returncode != 0:
            raise FFmpegError(f"首帧相似度检测失败：\n{(p.stderr or '')[-1500:]}")
        v = parse_ssim(p.stderr)
        if v is None:
            raise FFmpegError("首帧相似度检测没有结果")
        # 第二个信号：整体平均颜色。大块纯色区域的 SSIM 对颜色不敏感（纯红对纯绿 U / V 仍有 0.6），
        # 平均色差大就按色差算
        return min(v, self._mean_color_similarity(video, image))

    def frame_ssim(self, video: Path, at: float, image: Path) -> float:
        """视频第 at 秒那一帧和一张图有多像（SSIM，取 Y / U / V 里最低的；和 first_frame_ssim 同一套：
        都缩到 SSIM_SIZE、yuv444p）。查中段把参考图叠进画面用（pipeline/videos._ghost_qc）。"""
        w, h = SSIM_SIZE
        graph = (f"[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={w}:{h},format=yuv444p[a];"
                 f"[1:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale={w}:{h},format=yuv444p[b];"
                 "[a][b]ssim")
        seek = ["-ss", f"{max(float(at), 0.0):.3f}"] if at and at > 0 else []
        p = self._exec([self.ffmpeg, "-hide_banner", "-nostats", *seek, "-i", str(video),
                        "-i", str(image), "-an", "-filter_complex", graph, "-frames:v", "1",
                        "-f", "null", "-"])
        if p.returncode != 0:
            raise FFmpegError(f"帧相似度检测失败：\n{(p.stderr or '')[-1500:]}")
        v = parse_ssim(p.stderr)
        if v is None:
            raise FFmpegError("帧相似度检测没有结果")
        return v

    def _mean_color_similarity(self, video: Path, image: Path) -> float:
        """两张图各缩成 1×1 像素（平均颜色）比 RGB 距离：1 = 一样，0 = 黑对白。"""
        graph = ("[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,scale=1:1:flags=area,format=rgb24[a];"
                 "[1:v]setpts=PTS-STARTPTS,scale=1:1:flags=area,format=rgb24[b];[a][b]hstack=2")
        p = self._exec([self.ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(video),
                        "-i", str(image), "-an", "-filter_complex", graph, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], text=False)
        if p.returncode != 0 or len(p.stdout or b"") < 6:
            err = (p.stderr or b"").decode("utf-8", "replace")[-1500:]
            raise FFmpegError(f"首帧颜色比对失败：{err}")
        a, b = p.stdout[:3], p.stdout[3:6]
        dist = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 / (255 * 3 ** 0.5)
        return max(0.0, 1.0 - dist)

    def trim_silence(self, src: Path, dst: Path) -> Path:
        """剪掉音频首尾静音（配音用）。"""
        from backend.post.dub import build_trim_silence_cmd
        self.run(build_trim_silence_cmd(self.ffmpeg, src, dst))
        return Path(dst)

    def last_frame(self, video: Path, out_png: Path) -> Path:
        out_png = Path(out_png)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        self.run([self.ffmpeg, "-y", "-sseof", "-1", "-i", str(video), "-update", "1",
                  "-q:v", "2", str(out_png)])
        if not out_png.exists():
            raise FFmpegError(f"没截到尾帧：{video}")
        return out_png

    def duration(self, path: Path) -> float:
        """容器时长（音频文件用）。"""
        out = self.run([self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)])
        try:
            return float(out.strip())
        except ValueError:
            raise FFmpegError(f"读不出时长：{path}") from None

    def assemble(self, clips: list[Path], out: Path, *, width: int, height: int, fps: int,
                 transitions: list[str], work_dir: Path,
                 max_seconds: list[float | None] | None = None) -> Assembled:
        """max_seconds[i] 不为空时第 i 镜只取前这么多秒（按分镜时长裁尾）。"""
        if not clips:
            raise FFmpegError("没有片段可合成")
        out, work_dir = Path(out), Path(work_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        limits = list(max_seconds or [])
        flags = [self.has_audio(c) for c in clips]
        any_audio = any(flags)
        norm: list[Path] = []
        durs: list[float] = []
        for i, (clip, has) in enumerate(zip(clips, flags)):
            self.check_canceled()
            dst = work_dir / f"n{i:03d}.mp4"
            mode = "keep" if has else ("silent" if any_audio else "none")
            src_dur = self.video_duration(clip)
            lim = limits[i] if i < len(limits) else None
            dur = min(src_dur, lim) if lim else src_dur
            self.run(build_normalize_cmd(self.ffmpeg, clip, dst, width=width, height=height,
                                         fps=fps, duration=dur, audio=mode))
            norm.append(dst)
            durs.append(self.video_duration(dst))
        _, starts, total = xfade_timeline(durs, transitions, fps)
        result = Assembled(out, durs, starts, total)
        if len(norm) == 1:
            shutil.copyfile(norm[0], out)
            return result
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
        return result
