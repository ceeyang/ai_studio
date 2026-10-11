"""双语字幕（中文在上、高棉文在下）和片尾卡的 ASS 文本。用 libass 渲染：高棉文要靠 HarfBuzz 做字符重排。

移植自 vglib/subs.py：中文和高棉文放进同一条事件、用 \\N 分行，libass 把它们当一个文本块堆叠，
高棉文再长也只会往上顶，不会和中文行叠在一起。
"""
from __future__ import annotations

from dataclasses import dataclass

STYLE_FORMAT = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
EVENT_FORMAT = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
MODES = ("zh", "zh+km")


@dataclass
class Cue:
    start: float
    end: float
    zh: str
    km: str
    speaker: str = ""


def ass_time(sec: float) -> str:
    cs = int(round(max(sec, 0.0) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def wrap_zh(text: str, max_chars: int) -> str:
    """中文没有空格，libass 不会自动折行；超长就在最后一个标点后面断开。"""
    if len(text) <= max_chars:
        return text
    cut = -1
    for i, ch in enumerate(text[:max_chars]):
        if ch in "，、。？！；：,?!":
            cut = i
    if cut <= 0:
        cut = max_chars - 1
    head, tail = text[:cut + 1], text[cut + 1:]
    return head + ("\\N" + wrap_zh(tail, max_chars) if tail else "")


# 烧进画面的中文字幕：行尾的这些标点去掉（字幕习惯，句号逗号挂在行尾很碍眼）；？！…… 表语气，保留
LINE_END_PUNCT = "。，、；：,;:"


def burned_zh(text: str, max_chars: int) -> str:
    """烧进画面的中文字幕：先去掉句末的「。，、；：」再折行，折行处挂在行尾的逗号顿号也去掉。"""
    body = (text or "").strip().rstrip(LINE_END_PUNCT).strip()
    if not body:
        return ""
    return "\\N".join(line.rstrip(LINE_END_PUNCT) for line in wrap_zh(body, max_chars).split("\\N"))


def subtitle_zh(d: dict) -> str:
    """中文字幕显示原句：按高棉语时长改写过的中文（zh）只是为了让嘴型对上配音，凑长了（「那个、就是说」），
    观众要看的是原意（zh_original）。"""
    return (d.get("zh_original") or d.get("zh") or "").strip()


def timeline_cues(shots_dialogue: list[list[dict]], starts: list[float],
                  durations: list[float]) -> list[Cue]:
    """把每镜的台词（相对本镜开头）换算到成片时间轴：加上本镜起点，end 不超过本镜结尾，相邻两条不重叠。"""
    cues: list[Cue] = []
    for dialogue, start, dur in zip(shots_dialogue, starts, durations):
        for d in dialogue or []:
            a = start + float(d.get("start") or 0)
            b = start + min(float(d.get("end") or 0), dur)
            if b - a < 0.05:
                continue
            cues.append(Cue(a, b, subtitle_zh(d), (d.get("km") or "").strip(),
                            d.get("speaker") or ""))
    cues.sort(key=lambda c: c.start)
    for cur, nxt in zip(cues, cues[1:]):
        cur.end = min(cur.end, nxt.start)
    return cues


def dub_cues(rows: list[dict], starts: list[float], shots_dialogue: list[list[dict]], *,
             min_seconds: float = 1.0, tail: float = 0.3) -> list[Cue]:
    """配音版的字幕跟着高棉语配音走：每条从这句配音开始播的地方起，到配音结束再留 tail 秒
    （至少 min_seconds，好读），不压到下一条。rows 是配音清单（shot / index / offset / seconds）。"""
    cues: list[Cue] = []
    for r in rows:
        dlg = shots_dialogue[r["shot"]] if r["shot"] < len(shots_dialogue) else []
        d = dlg[r["index"]] if r["index"] < len(dlg) else {}
        a = starts[r["shot"]] + float(r["offset"])
        b = a + max(float(r["seconds"]) + tail, min_seconds)
        cues.append(Cue(a, b, subtitle_zh(d), (d.get("km") or "").strip(),
                        d.get("speaker") or ""))
    cues.sort(key=lambda c: c.start)
    for cur, nxt in zip(cues, cues[1:]):
        cur.end = min(cur.end, nxt.start)
    return cues


def _head(width: int, height: int, styles: list[str]) -> list[str]:
    return ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
            "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]", STYLE_FORMAT,
            *styles, "", "[Events]", EVENT_FORMAT]


def build_subtitles_ass(cues: list[Cue], width: int, height: int, *, mode: str, zh_font: str,
                        km_font: str) -> str:
    """mode：zh 只烧中文；zh+km 中文在上、高棉文在下。字幕块底边在画面下方约 21% 处（样板视频的位置）。"""
    if mode not in MODES:
        raise ValueError(f"字幕模式只能是 {MODES}，收到 {mode!r}")
    zh_size = max(36, int(height * 0.038))
    km_size = max(28, int(height * 0.031))
    margin = int(height * 0.215)
    styles = [
        f"Style: ZH,{zh_font},{zh_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,3,0,2,40,40,{margin},1",
        f"Style: KM,{km_font},{km_size},&H00F2F2F2,&H000000FF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,2.5,0,2,40,40,{margin},1"]
    max_zh = max(8, int(width / (zh_size * 1.08)))
    events = []
    for c in cues:
        parts = []
        zh = burned_zh(c.zh, max_zh)
        if zh:
            parts.append(zh)
        if mode == "zh+km" and c.km:
            parts.append("{\\rKM}" + c.km)
        if parts:
            # 中高两行之间插一行小字号空行：高棉文的上标元音不会顶到中文
            events.append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.end)},ZH,,0,0,0,,"
                          + "\\N{\\fs14}\\h\\N".join(parts))
    return "\n".join(_head(width, height, styles) + events) + "\n"


def build_endcard_ass(lines: list[dict], width: int, height: int, seconds: float, *,
                      default_font: str) -> str:
    """片尾卡：每行自己的字体、字号、粗细，整块垂直居中，文字淡入。空行只占高度（行高 = 字号 × 1.35）。"""
    styles = [f"Style: CARD,{default_font},40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
              "0,0,0,0,100,100,0,0,1,0,0,5,40,40,0,1"]
    heights = [int(int(line.get("size") or 40) * 1.35) for line in lines]
    y = (height - sum(heights)) // 2
    events = []
    for line, h in zip(lines, heights):
        text = (line.get("text") or "").strip()
        size = int(line.get("size") or 40)
        if text:
            font = line.get("font") or default_font
            bold = 1 if line.get("bold") else 0
            events.append(
                f"Dialogue: 0,{ass_time(0)},{ass_time(seconds)},CARD,,0,0,0,,"
                f"{{\\an5\\pos({width // 2},{y + size // 2})\\fn{font}\\fs{size}\\b{bold}"
                f"\\fad(500,0)}}{text}")
        y += h
    return "\n".join(_head(width, height, styles) + events) + "\n"
