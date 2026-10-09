"""成片收尾的 ffmpeg 命令：烧字幕、接片尾卡（一次编码完成）。

ass 滤镜的路径写在滤镜串里，Windows 盘符的冒号没法可靠转义，所以 ASS 文件和字体目录都放在工作目录、
以相对路径引用，命令在工作目录里执行（FFmpeg.run 的 cwd）。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
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


def build_finish_cmd(ffmpeg: str, src, out, *, subs_ass: str | None, card: CardSpec | None,
                     has_audio: bool, fonts_dir: str = "_fonts") -> list[str]:
    cmd = [ffmpeg, "-y", "-i", str(src)]
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p"]
    if card is None:
        if not subs_ass:
            raise ValueError("既没有字幕也没有片尾卡，不需要收尾")
        cmd += ["-vf", f"ass={subs_ass}:fontsdir={fonts_dir}", "-map", "0:v:0"]
        if has_audio:
            cmd += ["-map", "0:a:0", "-c:a", "copy"]
        return cmd + enc + ["-movflags", "+faststart", str(out)]

    cmd += ["-f", "lavfi", "-i",
            f"color=c={card.color}:s={card.width}x{card.height}:r={card.fps}:d={card.seconds:.3f}"]
    if has_audio:
        cmd += ["-f", "lavfi", "-t", f"{card.seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    main = f"ass={subs_ass}:fontsdir={fonts_dir}," if subs_ass else ""
    parts = [f"[0:v]{main}setsar=1,format=yuv420p[mv]",
             f"[1:v]ass={card.ass}:fontsdir={fonts_dir},fade=t=in:st=0:d={card.fade:.3f},"
             f"setsar=1,format=yuv420p[cv]"]
    if has_audio:
        parts += [f"[0:a]{AUDIO_FMT}[ma]", f"[2:a]{AUDIO_FMT}[ca]",
                  "[mv][ma][cv][ca]concat=n=2:v=1:a=1[v][a]"]
    else:
        parts.append("[mv][cv]concat=n=2:v=1:a=0[v]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    return cmd + enc + ["-r", str(card.fps), "-movflags", "+faststart", str(out)]
