"""成片收尾的 ffmpeg 命令：烧字幕、烧 AI 标识角标、接片尾卡、写元数据（一次编码完成）。

ass 滤镜的路径写在滤镜串里，Windows 盘符的冒号没法可靠转义，所以 ASS 文件和字体目录都放在工作目录、
以相对路径引用，命令在工作目录里执行（FFmpeg.run 的 cwd）。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

AUDIO_FMT = "aformat=sample_rates=48000:channel_layouts=stereo"


@dataclass
class CardSpec:
    ass: str
    seconds: float
    width: int
    height: int
    fps: int
    color: str = "0x0b1a33"
    fade: float = 0.5


def stage_fonts(fonts_dir: Path, names: list[str], work_dir: Path, rel: str = "_fonts") -> str:
    """把用到的字体拷进 work_dir/_fonts，返回相对目录名。缺的跳过，libass 会回退到系统字体。"""
    dst = Path(work_dir) / rel
    dst.mkdir(parents=True, exist_ok=True)
    for name in names:
        src = Path(fonts_dir) / name
        if src.is_file() and not (dst / name).exists():
            shutil.copyfile(src, dst / name)
    return rel


@lru_cache(maxsize=16)
def ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    """这个 ffmpeg 有没有某个滤镜（如 ass：要编译进 libass。Homebrew 的 ffmpeg 8 以后默认不带）。"""
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(len(p) > 1 and p[1] == name for p in (line.split() for line in out.splitlines()))


def metadata_args(metadata: dict[str, str] | None) -> list[str]:
    """-metadata 键=值（参数直接交给 subprocess，不过 shell，中文和引号原样写）；值里的换行压成空格。"""
    out: list[str] = []
    for k, v in (metadata or {}).items():
        out += ["-metadata", f"{k}={' '.join(str(v).split())}"]
    return out


def _movflags(metadata: dict[str, str] | None) -> list[str]:
    # use_metadata_tags：mp4 默认只写认得的键（title / comment 等），自定义的 aigc_* 要靠它才留得下
    return ["-movflags", "+faststart+use_metadata_tags" if metadata else "+faststart"]


def build_finish_cmd(ffmpeg: str, src, out, *, subs_ass: str | None, card: CardSpec | None,
                     has_audio: bool, fonts_dir: str = "_fonts", label_ass: str | None = None,
                     label_on_card: bool = False,
                     metadata: dict[str, str] | None = None) -> list[str]:
    """收尾编码：烧字幕、烧 AI 标识角标（label_ass）、接片尾卡、写元数据。

    角标叠在正片上（时间从正片第 0 秒算），label_on_card=True（整集常显）时片尾卡上也叠；
    什么画面都不用改、只写元数据时直接拷贝流，不重新编码。"""
    cmd = [ffmpeg, "-y", "-i", str(src)]
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
    meta = metadata_args(metadata)
    main_filters = [f"ass={a}:fontsdir={fonts_dir}" for a in (subs_ass, label_ass) if a]
    if card is None:
        if not main_filters:
            if not metadata:
                raise ValueError("既没有字幕、角标也没有片尾卡，不需要收尾")
            cmd += ["-map", "0:v:0"] + (["-map", "0:a:0"] if has_audio else []) + ["-c", "copy"]
            return cmd + meta + _movflags(metadata) + [str(out)]
        cmd += ["-vf", ",".join(main_filters), "-map", "0:v:0"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-c:a", "copy"]
        return cmd + enc + meta + _movflags(metadata) + [str(out)]

    cmd += ["-f", "lavfi", "-i",
            f"color=c={card.color}:s={card.width}x{card.height}:r={card.fps}:d={card.seconds:.3f}"]
    if has_audio:
        cmd += ["-f", "lavfi", "-t", f"{card.seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    main = "".join(f"{f}," for f in main_filters)
    on_card = f",ass={label_ass}:fontsdir={fonts_dir}" if label_ass and label_on_card else ""
    parts = [f"[0:v]{main}setsar=1,format=yuv420p[mv]",
             f"[1:v]ass={card.ass}:fontsdir={fonts_dir},fade=t=in:st=0:d={card.fade:.3f}{on_card},"
             f"setsar=1,format=yuv420p[cv]"]
    if has_audio:
        parts += [f"[0:a]{AUDIO_FMT}[ma]", f"[2:a]{AUDIO_FMT}[ca]",
                  "[mv][ma][cv][ca]concat=n=2:v=1:a=1[v][a]"]
    else:
        parts.append("[mv][cv]concat=n=2:v=1:a=0[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    return cmd + enc + ["-r", str(card.fps)] + meta + _movflags(metadata) + [str(out)]
