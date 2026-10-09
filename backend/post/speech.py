"""在 H3 片段自己的音轨里找"有人在说话"的段落，用来把高棉语配音放到实际开口的位置。

只看能量不行：H3 的车间环境声（宽带底噪、扳手声）常常和人声一样响。这里按 40ms 一帧判断"浊音"：
能量够（≥ min_db）且波形有周期性（归一化自相关在 75–400Hz 基频对应的延迟上有明显峰值，≥ periodicity）。
人声的元音都是浊音，底噪和大部分机械声不是。相邻浊音帧之间停顿不超过 merge_gap 的并成一句
（辅音、换气），太短的丢掉。对齐时只在计划起点前后 max_shift 秒内找，找不到就退回台词的 start。
"""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
VOICE_BAND = "highpass=f=70,lowpass=f=4000"


def build_pcm_cmd(ffmpeg: str, src, *, limit: float | None = None,
                  sample_rate: int = SAMPLE_RATE) -> list[str]:
    """把第一条音轨解成单声道 float32 原始数据（先带通到人声频段）写到 stdout。"""
    cmd = [ffmpeg, "-v", "error", "-i", str(src)]
    if limit:
        cmd += ["-t", f"{limit:.3f}"]
    return cmd + ["-map", "0:a:0", "-af", VOICE_BAND, "-ac", "1", "-ar", str(sample_rate),
                  "-f", "f32le", "-"]


def voiced_frames(x: np.ndarray, sr: int, *, frame: float = 0.04, hop: float = 0.01,
                  min_db: float = -45.0, periodicity: float = 0.45, fmin: float = 75.0,
                  fmax: float = 400.0) -> np.ndarray:
    """每一帧（hop 秒一帧）是不是浊音。"""
    n, h = int(frame * sr), int(hop * sr)
    if len(x) < n:
        return np.zeros(0, dtype=bool)
    idx = np.arange(0, len(x) - n + 1, h)[:, None] + np.arange(n)[None, :]
    fr = x[idx].astype(np.float64)
    fr = fr - fr.mean(axis=1, keepdims=True)
    energy = (fr ** 2).mean(axis=1)
    db = 10 * np.log10(np.maximum(energy, 1e-12))
    spec = np.fft.rfft(fr * np.hanning(n), 2 * n, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :n]
    lo, hi = int(sr / fmax), min(int(sr / fmin), n - 1)
    peak = ac[:, lo:hi + 1].max(axis=1) / np.maximum(ac[:, 0], 1e-12)
    return (db >= min_db) & (peak >= periodicity)


def frames_to_segments(voiced: np.ndarray, hop: float, frame: float, *, merge_gap: float = 0.25,
                       min_len: float = 0.12) -> list[tuple[float, float]]:
    segs: list[tuple[float, float]] = []
    start = None
    for i, v in enumerate(list(voiced) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append((start * hop, (i - 1) * hop + frame))
            start = None
    merged: list[tuple[float, float]] = []
    for a, b in segs:
        if merged and a - merged[-1][1] <= merge_gap + 1e-9:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_len - 1e-9]


def speech_segments_from_pcm(x: np.ndarray, sr: int = SAMPLE_RATE, *, min_db: float = -45.0,
                             periodicity: float = 0.45, merge_gap: float = 0.25,
                             min_len: float = 0.12, frame: float = 0.04,
                             hop: float = 0.01) -> list[tuple[float, float]]:
    v = voiced_frames(x, sr, frame=frame, hop=hop, min_db=min_db, periodicity=periodicity)
    return frames_to_segments(v, hop, frame, merge_gap=merge_gap, min_len=min_len)


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
            used.update(range(best + 1))   # 之后的句子不能再用这一段及之前的段
            t = segments[best][0]
        out.append(round(t, 3))
        last = t
    return out
