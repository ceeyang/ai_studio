"""外挂配音：Edge TTS 按角色音色念高棉语台词，塞进台词窗口，叠在压低的原音轨上。

移植自 vglib/dub.py。H3 只会说主流语种，让它说高棉语出来的是"东南亚像音"，所以人声交给
Edge TTS（km-KH-PisethNeural 男 / km-KH-SreymomNeural 女）。代价：口型和外挂人声对不上。
原音轨分两档：台词窗口外压到 bed_db（环境音还在），窗口内再压到 duck_db——H3 自己的普通话人声也在
原音轨里，只压 14 dB 盖不住。
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path

from backend.post.music import LIMITER

SAMPLE_RATE = 48000


@dataclass
class DubLine:
    shot: int          # 第几镜（0 起）
    index: int         # 本镜第几句（0 起，按原台词序号）
    speaker: str
    text: str
    start: float       # 成片时间轴上的起点
    window: float      # 最多能占多少秒


def fit_rate(natural: float, window: float, max_rate: int) -> int:
    """原速念完要 natural 秒、窗口只有 window 秒时该提速多少（整数百分比，封顶 max_rate）。"""
    if window <= 0 or natural <= window:
        return 0
    return min(int(max_rate), int(math.ceil((natural / window - 1.0) * 100)) + 2)


def plan_dub_lines(shots_dialogue: list[list[dict]], starts: list[float], durations: list[float],
                   *, gap: float = 0.1, field: str = "km") -> list[DubLine]:
    """每句从台词 start 开始念；窗口到本镜下一句的 start（或本镜结尾）为止，再留 gap。没有译文的句子跳过。"""
    out: list[DubLine] = []
    for i, (dialogue, start, dur) in enumerate(zip(shots_dialogue, starts, durations)):
        rows = [(j, d) for j, d in enumerate(dialogue or []) if (d.get(field) or "").strip()]
        for n, (j, d) in enumerate(rows):
            t0 = float(d.get("start") or 0)
            nxt = float(rows[n + 1][1].get("start") or 0) if n + 1 < len(rows) else dur
            out.append(DubLine(i, j, d.get("speaker") or "", d[field].strip(), start + t0,
                               max(min(nxt, dur) - t0 - gap, 0.5)))
    return out


def build_dub_mix_cmd(ffmpeg: str, src, lines: list[tuple], out, *, bed_db: float | None,
                      duck_db: float, loudness: float | None = None) -> list[str]:
    """lines：[(音频文件, 成片起点秒, 时长秒)]。画面直接拷贝，只重编码音轨。

    bed_db=None：不要原声底，原音轨乘 0（只当时间轴用，保证成片长度不变），成片里只剩配音。

    loudness：混好后用 loudnorm 拉到这个响度（LUFS）。原音轨压低后整体会比普通话版轻十来个 LU。
    """
    cmd = [ffmpeg, "-y", "-i", str(src)]
    for path, _, _ in lines:
        cmd += ["-i", str(path)]
    fmt = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    bed = "[0:a]volume=0" if bed_db is None else f"[0:a]volume={float(bed_db):.1f}dB"
    extra = 0.0 if bed_db is None else float(duck_db) - float(bed_db)
    if lines and extra < 0:
        windows = "+".join(f"between(t,{max(st - 0.15, 0.0):.2f},{st + du + 0.25:.2f})"
                           for _, st, du in lines)
        bed += f",volume={extra:.1f}dB:enable='{windows}'"
    parts = [f"{bed},{fmt}[bed]"]
    labels = ["[bed]"]
    for i, (_, st, _) in enumerate(lines, start=1):
        parts.append(f"[{i}:a]{fmt},adelay={int(round(max(st, 0.0) * 1000))}:all=1[v{i}]")
        labels.append(f"[v{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0"
                 f":duration=first[mix]")
    if loudness is not None:
        parts.append(f"[mix]loudnorm=I={loudness:g}:TP=-1.5:LRA=11[a]")   # loudnorm 自己限真峰值
    else:
        parts.append(f"[mix]{LIMITER}[a]")   # 不拉响度时最后限幅，防削波
    return cmd + ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]",
                  "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
                  "-movflags", "+faststart", str(out)]


def build_trim_silence_cmd(ffmpeg: str, src, dst, *, threshold_db: float = -45.0) -> list[str]:
    """剪掉一句 TTS 首尾的静音（Edge TTS 句尾常带一秒多空白），输出 48k 立体声 wav，时长就是真正说话的长度。"""
    one = f"silenceremove=start_periods=1:start_threshold={threshold_db:g}dB:start_silence=0.03"
    return [ffmpeg, "-y", "-i", str(src), "-af", f"{one},areverse,{one},areverse",
            "-ar", str(SAMPLE_RATE), "-ac", "2", str(dst)]


def edge_tts_save(text: str, voice: str, rate_pct: int, out: Path) -> None:
    """Edge TTS 念一句存成 mp3。rate_pct 是整数百分比，0 = 原速。需要联网，不要 key。"""
    from edge_tts import Communicate

    async def go():
        await Communicate(text, voice=voice, rate=f"{int(rate_pct):+d}%",
                          connect_timeout=15).save(str(out))
    asyncio.run(go())
