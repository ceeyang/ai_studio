"""AI 生成内容标识（合规）：画面上的显式标识（角标）和成片文件里的隐式标识（元数据）。

依据：
- 广电总局：2026-07-01 起 AI 参与制作的微短剧，要在片头 / 每集明显位置标注「AI 辅助制作」等显式标识；
- 《人工智能生成合成内容标识办法》（2025-09-01 施行）+ GB 45438-2025：显式标识之外，文件元数据里要有
  隐式标识，写明生成合成内容属性、服务提供者（出品方）、内容编号等。

角标作为 ASS 字幕事件在收尾那一次编码里烧进画面（post/subtitles.py::build_label_ass），不另接片头卡，
字幕和配乐的时间都不会被推后。元数据用 ffmpeg -metadata 写进 mp4（-movflags use_metadata_tags 保留自定义键）。
"""
from __future__ import annotations

import json

DEFAULT_LABEL_TEXT = "本片由 AI 辅助制作"
DEFAULT_PRODUCER = "AI Studio"
LABEL_TAG = "AI辅助制作"
TOOL = "ai_studio"
# 不是 AI 生成的视频候选：用户自己上传的素材（引擎写 upload；空 = 来源不明，按非 AI 算，宁可少报）
NON_AI_ENGINES = frozenset({"upload", ""})


def project_aigc(raw: dict | None) -> dict:
    """项目的标识设置，缺的填默认值。"""
    raw = raw or {}
    return {"label_text": (raw.get("label_text") or "").strip() or DEFAULT_LABEL_TEXT,
            "producer": (raw.get("producer") or "").strip() or DEFAULT_PRODUCER}


def ai_ratio(engines: list[str]) -> int:
    """成片里 AI 生成的镜头占比（百分比，四舍五入）：按选定视频候选的引擎算，上传的素材不算 AI。"""
    if not engines:
        return 0
    ai = sum(1 for e in engines if (e or "").strip() not in NON_AI_ENGINES)
    return round(100 * ai / len(engines))


def work_id(slug: str, episode_number: int) -> str:
    return f"{slug}-E{episode_number:02d}"


def episode_title(project_title: str, episode_number: int, title: str = "") -> str:
    head = f"{project_title} 第{episode_number}集"
    return f"{head}：{title.strip()}" if (title or "").strip() else head


def build_metadata(*, title: str, producer: str, work: str, ratio: int,
                   created: str) -> dict[str, str]:
    """写进成片的元数据（键 → 值，顺序固定）。

    comment / description 是一段 JSON，普通播放器的「注释」里也能看到；aigc_* 是给程序读的自定义键；
    AIGC 按 GB 45438-2025 元数据隐式标识的字段名写（Label=1 表示 AI 生成合成内容）。
    """
    info = {"label": LABEL_TAG, "producer": producer, "work_id": work, "ai_ratio": ratio,
            "tool": TOOL, "created": created}
    blob = json.dumps(info, ensure_ascii=False, separators=(",", ":"))
    standard = json.dumps({"Label": "1", "ContentProducer": producer, "ProduceID": work,
                           "ReservedCode1": "", "ContentPropagator": "", "PropagateID": "",
                           "ReservedCode2": ""}, ensure_ascii=False, separators=(",", ":"))
    return {"title": title, "comment": blob, "description": blob,
            "aigc_label": LABEL_TAG, "aigc_producer": producer, "aigc_work_id": work,
            "aigc_ratio": str(ratio), "aigc_tool": TOOL, "AIGC": standard}
