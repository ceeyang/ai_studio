"""导出选项：API、CLI、界面和任务链共用。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SubtitleMode = Literal["none", "zh", "zh+km"]
DubMode = Literal["off", "km"]
AlignMode = Literal["timeline", "detected"]
LabelPosition = Literal["top_left", "top_right", "bottom_left", "bottom_right"]


class ExportOptions(BaseModel):
    subtitles: SubtitleMode = "none"   # 中文在上、高棉文在下
    dub: DubMode = "off"               # km：高棉语外挂配音，原音轨压低当环境底
    end_card: bool = False             # 接项目的片尾卡
    trim: bool = True                  # 按分镜时长裁掉台词说完后的多余尾巴
    bed: bool = True                   # 保留原声底：False = H3 原音轨整条静音（只剩配音）
    # detected：配音（不配音时是中文字幕和配乐压低）放到 H3 音轨里实际开口的位置
    align: AlignMode = "timeline"
    # 配乐（剧集上传的背景音乐，post/music.py）：音量、淡入淡出、有台词时自动压低
    music: bool = False
    music_db: float = Field(-18.0, ge=-40.0, le=6.0)
    music_fade_in: float = Field(1.0, ge=0.0, le=10.0)
    music_fade_out: float = Field(2.0, ge=0.0, le=10.0)
    music_duck: bool = True
    # AI 生成内容标识（post/aigc.py）：角标烧进画面（文字在项目设置里改，默认「本片由 AI 辅助制作」），
    # 默认左上角、片头 3 秒；aigc_label_always = 整集常显。aigc_metadata：成片文件里写隐式标识元数据
    aigc_label: bool = True
    aigc_label_position: LabelPosition = "top_left"
    aigc_label_seconds: float = Field(3.0, ge=1.0, le=60.0)
    aigc_label_always: bool = False
    aigc_metadata: bool = True

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
        if self.music:
            parts.append("bgm")
        if self.end_card:
            parts.append("card")
        return "".join(f"_{p}" for p in parts)


# 字幕模式 → 标签文字，照抄 frontend/src/pages/DirectorPage.tsx 的 SUBTITLE_TAG
SUBTITLE_LABELS = {"zh": "中文字幕", "zh+km": "中高字幕"}


def export_label(variant: str, settings: dict) -> str:
    """导出列表用的可读标签，如「9:16 · 中高字幕 · 高棉语配音 · 无原声底 · 配乐 · 片尾卡 · 37.1 秒」。

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
        # 不配音时对齐的是中文字幕（和配乐压低）
        parts.append("配音对齐说话" if settings.get("dub") == "km" else "字幕对齐说话")
    if settings.get("music") is True:
        parts.append("配乐")
    if settings.get("end_card") is True:
        parts.append("片尾卡")
    # AI 标识默认开着，不用每条都写；只标出关掉的、或者 ffmpeg 烧不了角标的
    if settings.get("aigc_label") is False:
        parts.append("无 AI 角标")
    elif settings.get("aigc_label_burned") is False:
        parts.append("AI 角标没烧上")
    duration = settings.get("duration")
    if isinstance(duration, (int, float)):
        parts.append(f"{duration:.1f} 秒")
    return " · ".join(parts)
