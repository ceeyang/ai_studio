"""新建剧集对话框的「AI 写梗概 / AI 续写」：按项目的风格预设、本集标题、目标时长和前几集的梗概，
同步调用 LLM 写一段剧情梗概（不进任务队列，结果直接回给对话框，由用户再改）。

三种写法（按框里已有的文字判断）：
- new：框是空的，按标题（或前情）从头写一个完整梗概；
- expand：框里只有一句话的点子，扩写成完整梗概；
- continue：框里已经有一段，接着往下写，只返回新增的部分（前端拼在末尾，不改用户写的）。

文本模型走设置页「按步骤单独设置 → AI 写梗概」（step = episode.idea），没设置时跟 config.yaml 的剧本任务。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import select

from backend.domain.models import Episode, Project
from backend.domain.style_presets import get_preset
from backend.queue.errors import JobError, NeedsInput

LLM_TASK = "script"          # config.yaml 的 llm.routes 按剧本任务走
STEP = "episode.idea"
IDEA_MAX_CHARS = 80          # 一句话的点子：不超过这么多字、最多一个句末标点
PREV_EPISODES = 2            # 前情取最近几集
PREV_SUMMARY_CHARS = 300     # 每集前情最多多少字（太长的保留开头和结尾）
_SENTENCE_END = re.compile(r"[。！？!?；;…\n]+")


class EpisodeIdea(BaseModel):
    text: str


class IdeaFailed(RuntimeError):
    """LLM 调用失败（模型连不上、输出一直不合格）。路由把它变成 502 和中文说明。"""


@dataclass
class IdeaResult:
    text: str
    mode: str


def idea_mode(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "new"
    sentences = [x for x in _SENTENCE_END.split(t) if x.strip()]
    if len(t) <= IDEA_MAX_CHARS and len(sentences) <= 1:
        return "expand"
    return "continue"


def length_range(target_seconds: float) -> tuple[int, int]:
    """梗概大约多少个汉字：10 秒约 2–4 句（50–100 字），60 秒约一段（180–300 字）。"""
    sec = max(float(target_seconds or 0), 1.0)
    lo = int(min(max(50, sec * 3), 800))
    hi = int(min(max(100, sec * 5), 1200))
    return lo, max(hi, lo + 50)


def _summary(text: str, n: int = PREV_SUMMARY_CHARS) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= n:
        return t
    head = n * 2 // 5
    return t[:head] + "……" + t[-(n - head):]   # 结尾的悬念最要紧，多留一些


def previous_episodes(s, project_id: int, number: int) -> list[dict]:
    """同项目里这一集之前、最近的 PREV_EPISODES 集（有原文的），按集号从前往后。"""
    rows = s.scalars(select(Episode).where(Episode.project_id == project_id,
                                           Episode.number < number)
                     .order_by(Episode.number.desc())).all()
    picked = [e for e in rows if (e.source_text or "").strip()][:PREV_EPISODES]
    return [{"number": e.number, "title": e.title or "", "summary": _summary(e.source_text)}
            for e in reversed(picked)]


def _strip_repeat(existing: str, out: str) -> str:
    """续写时模型偶尔把用户写的又抄一遍：去掉开头重复的部分。"""
    base = existing.strip()
    if base and out.startswith(base):
        return out[len(base):].lstrip()
    return out


def write_episode_idea(sv, slug: str, *, title: str = "", target_seconds: float = 30.0,
                       number: int | None = None, text: str = "") -> IdeaResult:
    title = (title or "").strip()
    with sv.db.session() as s:
        proj = s.scalar(select(Project).where(Project.slug == slug))
        if proj is None:
            raise LookupError(f"找不到项目 {slug}")
        if number is None:
            last = s.scalar(select(Episode.number).where(Episode.project_id == proj.id)
                            .order_by(Episode.number.desc()).limit(1))
            number = (last or 0) + 1
        previous = previous_episodes(s, proj.id, number)
        mode = idea_mode(text)
        if mode == "new" and not title and not previous:
            raise JobError("先写本集标题，或在框里写一句点子，AI 才知道要写什么")
        preset = get_preset(proj.style_preset)
        lo, hi = length_range(target_seconds)
        system, user = sv.prompts(proj.slug).render(
            "episode_idea", project_title=proj.title, preset_key=preset.key,
            preset_label=preset.label, style_label=preset.style_label, title=title,
            target_seconds=float(target_seconds), number=number, mode=mode,
            text=(text or "").strip(), previous=previous, len_lo=lo, len_hi=hi)

    def check(out: EpisodeIdea) -> None:
        if not out.text.strip():
            raise ValueError("text 是空的，要写出梗概正文")

    try:
        out = sv.llm.generate(LLM_TASK, system, user, EpisodeIdea, check=check, step=STEP)
    except NeedsInput:
        raise JobError("「AI 写梗概」现在用的文本模型是手动粘贴，没法在对话框里直接写。"
                       "到「设置 → 按步骤单独设置」给「AI 写梗概（新建剧集）」选一个文本模型") from None
    except Exception as e:   # 模型连不上、输出一直不合格、命令超时……都给一句中文原因
        raise IdeaFailed(f"AI 写梗概失败：{e}") from None
    result = out.text.strip()
    if mode == "continue":
        result = _strip_repeat(text, result)
        if not result:
            raise IdeaFailed("AI 写梗概失败：模型只把已有的内容抄了一遍，没有往下写，再试一次")
    return IdeaResult(text=result, mode=mode)
