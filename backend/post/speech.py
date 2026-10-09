"""在 H3 片段自己的音轨里找"有人在说话"的段落，用来把高棉语配音放到实际开口的位置。

用 ffmpeg silencedetect（先带通到人声频段，压掉低频机器声和高频嘶声），静音段的补集就是说话段；
停顿很短的两段并成一句，太短的爆点（扳手声、敲击）丢掉。能量法分不清人声和响亮的环境声，
所以对齐时只在计划时间附近 max_shift 秒内找，找不到就退回台词的 start。
"""
from __future__ import annotations

from backend.engines.ffmpeg import SILENCE_END, SILENCE_START

VOICE_BAND = "highpass=f=150,lowpass=f=4000"


def build_speech_detect_cmd(ffmpeg: str, src, *, noise_db: float, min_silence: float,
                            limit: float | None = None) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-i", str(src)]
    if limit:
        cmd += ["-t", f"{limit:.3f}"]
    return cmd + ["-map", "0:a:0", "-af",
                  f"{VOICE_BAND},silencedetect=noise={noise_db:g}dB:d={min_silence:g}",
                  "-f", "null", "-"]


def parse_speech_segments(log: str, duration: float, *, merge_gap: float = 0.25,
                          min_len: float = 0.12) -> list[tuple[float, float]]:
    starts = [float(x) for x in SILENCE_START.findall(log)]
    ends = [float(x) for x in SILENCE_END.findall(log)]
    silences = [(max(s, 0.0), ends[i] if i < len(ends) else duration) for i, s in enumerate(starts)]
    speech: list[tuple[float, float]] = []
    t = 0.0
    for a, b in silences:
        if a - t > 1e-6:
            speech.append((t, a))
        t = max(t, b)
    if duration - t > 1e-6:
        speech.append((t, duration))
    merged: list[tuple[float, float]] = []
    for a, b in speech:
        if merged and a - merged[-1][1] <= merge_gap + 1e-9:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_len - 1e-9]


def align_onsets(planned: list[float], segments: list[tuple[float, float]], *,
                 max_shift: float = 1.5, min_spacing: float = 0.3) -> list[float]:
    """按顺序给每句挑一段说话的起点：离计划起点最近、在上一句之后、每段只用一次；找不到退回计划起点。"""
    out: list[float] = []
    used: set[int] = set()
    last = -1e9
    for p in planned:
        best = None
        for k, (a, _) in enumerate(segments):
            if k in used or a < last + min_spacing - 1e-9 or abs(a - p) > max_shift + 1e-9:
                continue
            if best is None or abs(a - p) < abs(segments[best][0] - p):
                best = k
        if best is None:
            t = p
        else:
            used.add(best)
            t = segments[best][0]
            # 之后的句子不能再用这一段之前的段
            used.update(k for k in range(best))
        out.append(round(t, 3))
        last = t
    return out
