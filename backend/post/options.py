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
