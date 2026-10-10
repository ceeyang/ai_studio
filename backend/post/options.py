"""导出选项：API、CLI、界面和任务链共用。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SubtitleMode = Literal["none", "zh", "zh+km"]
DubMode = Literal["off", "km"]
AlignMode = Literal["timeline", "detected"]


class ExportOptions(BaseModel):
    subtitles: SubtitleMode = "none"   # 中文在上、高棉文在下
    dub: DubMode = "off"               # km：高棉语外挂配音，原音轨压低当环境底
    end_card: bool = False             # 接项目的片尾卡
    trim: bool = True                  # 按分镜时长裁掉台词说完后的多余尾巴
    bed: bool = True                   # 保留原声底：False = H3 原音轨整条静音（只剩配音）
    align: AlignMode = "timeline"      # detected：配音放到 H3 音轨里实际开口的位置

    @property
    def needs_km(self) -> bool:
        return self.subtitles == "zh+km" or self.dub == "km"

    def tag(self) -> str:
        """文件名里的变体标记，如 _zhkm_dubkm_card。"""
        parts = []
        if self.subtitles != "none":
            parts.append(self.subtitles.replace("+", ""))
        if self.dub != "off":
            parts.append(f"dub{self.dub}")
        if not self.bed:
            parts.append("nobed")
        if self.align == "detected":
            parts.append("aligned")
        if self.end_card:
            parts.append("card")
        return "".join(f"_{p}" for p in parts)


# 字幕模式 → 标签文字，照抄 frontend/src/pages/DirectorPage.tsx 的 SUBTITLE_TAG
SUBTITLE_LABELS = {"zh": "中文字幕", "zh+km": "中高字幕"}


def export_label(variant: str, settings: dict) -> str:
    """导出列表用的可读标签，如「9:16 · 中高字幕 · 高棉语配音 · 无原声底 · 片尾卡 · 37.1 秒」。

    拼法照抄前端 ExportsPanel 的 exportTags（同文件 25-35 行）：画幅在最前，其余按条件追加，
    用「 · 」连接——这样导出页之后展示的标签和导演台现有成片列表看到的一致。
    """
    parts = [variant.replace("x", ":")]
    subs = SUBTITLE_LABELS.get(settings.get("subtitles"))
    if subs:
        parts.append(subs)
    if settings.get("dub") == "km":
        parts.append("高棉语配音")
    if settings.get("bed") is False:
        parts.append("无原声底")
    if settings.get("align") == "detected":
        parts.append("配音对齐说话")
    if settings.get("end_card") is True:
        parts.append("片尾卡")
    duration = settings.get("duration")
    if isinstance(duration, (int, float)):
        parts.append(f"{duration:.1f} 秒")
    return " · ".join(parts)
