"""配乐：把剧集的背景音乐垫在成片下面。

- 音乐循环到成片长度（-stream_loop -1 再按成片时长截断），整体压到 volume_db；
- 开头淡入 fade_in 秒、结尾淡出 fade_out 秒（片尾卡在之后接上，那段没有配乐）；
- 有台词时自动压低：在台词时间窗上做音量自动化（不是 sidechaincompress——H3 原声里的环境声、
  动作声也会触发侧链压缩，而我们准确知道哪里在说话）。每个窗口前后各有 ramp 秒的斜坡，
  不会一下子掉下去、一下子弹回来；相邻、重叠的窗口先合并。
- 画面直接拷贝，只重编码音轨；成片原来没有音轨时成片里只有配乐。
"""
from __future__ import annotations

SAMPLE_RATE = 48000
DUCK_DB = -10.0       # 台词窗口里配乐再压低多少
DUCK_RAMP = 0.3       # 压低 / 恢复用多少秒
PAD_BEFORE = 0.15     # 台词开始前多早开始压
PAD_AFTER = 0.25      # 台词结束后多晚开始恢复
FMT = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"


def duck_windows(spans, *, total: float | None = None, pad_before: float = PAD_BEFORE,
                 pad_after: float = PAD_AFTER) -> list[tuple[float, float]]:
    """台词 (起, 止) → 要压低配乐的时间窗：前后各留一点余量，按成片长度截断，重叠或挨着的合并。"""
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans if float(b) > float(a)):
        a, b = max(a - pad_before, 0.0), b + pad_after
        if total is not None:
            b = min(b, float(total))
            if a >= b:
                continue
        if out and a <= out[-1][1] + DUCK_RAMP:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def duck_expr(windows: list[tuple[float, float]], duck_db: float = DUCK_DB,
              ramp: float = DUCK_RAMP) -> str:
    """volume 滤镜的增益表达式（eval=frame）：窗口里是 10^(duck_db/20)，窗口外是 1，边上 ramp 秒线性过渡。"""
    k = 10 ** (float(duck_db) / 20)
    dips = "+".join(f"clip((t-{a - ramp:.3f})/{ramp:g},0,1)*clip(({b + ramp:.3f}-t)/{ramp:g},0,1)"
                    for a, b in windows)
    return f"1-{1 - k:.4f}*min(1,{dips})"


def build_music_mix_cmd(ffmpeg: str, src, music, out, *, total: float, volume_db: float,
                        fade_in: float, fade_out: float, windows: list[tuple[float, float]],
                        has_audio: bool, duck_db: float = DUCK_DB) -> list[str]:
    """把 music 混进 src（成片主体，total 秒）。windows 为空时不压低（没台词或没勾「有台词时压低」）。"""
    total = max(float(total), 0.1)
    chain = (f"[1:a]{FMT},atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
             f"volume={float(volume_db):.1f}dB")
    if windows:
        chain += f",volume='{duck_expr(windows, duck_db)}':eval=frame"
    fi = min(max(float(fade_in), 0.0), total)
    fo = min(max(float(fade_out), 0.0), total)
    if fi > 0:
        chain += f",afade=t=in:st=0:d={fi:.2f}"
    if fo > 0:
        chain += f",afade=t=out:st={total - fo:.3f}:d={fo:.2f}"
    parts = [chain + "[bgm]"]
    if has_audio:
        parts += [f"[0:a]{FMT}[orig]",
                  "[orig][bgm]amix=inputs=2:normalize=0:dropout_transition=0:duration=first[a]"]
    else:
        parts.append("[bgm]anull[a]")
    return [ffmpeg, "-y", "-i", str(src), "-stream_loop", "-1", "-i", str(music),
            "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE),
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
