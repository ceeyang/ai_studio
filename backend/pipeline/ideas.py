"""从一个点子出片（docs/specs/2026-10-11-idea-to-video-design.md）：首页向导用。

- options：一句话点子 → 几个差异明显的故事方向（卡片）；「再来几个」把看过的标题带上让模型避开。
- expand：选中的方向（一个，或两个合并）→ 片名 + 完整梗概；带 current + instruction 时按修改要求改写。
- submit_autopilot：剧集分镜做完后「继续出片」：出镜角色没有定妆照的各出 1 张，首帧 → 视频 → 导出接在后面。

options / expand 同步调用文本模型（不进任务队列，结果直接回给向导页），走设置页「按步骤单独设置 →
AI 写梗概」（step = episode.idea），没设置时跟 config.yaml 的剧本任务——和新建剧集的「AI 写梗概」同一个。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.domain.style_presets import get_preset
from backend.llm.prompts import PromptLibrary
from backend.pipeline.episode_idea import LLM_TASK, STEP, IdeaFailed, length_range
from backend.queue.errors import JobError, NeedsInput

OPTION_COUNT = 4
MAX_EXCLUDE = 24            # 「再来几个」最多带多少个看过的标题（太多了提示词太长）


class IdeaOption(BaseModel):
    title: str = Field(max_length=60)
    logline: str = Field(max_length=400)
    hook: str = Field("", max_length=300)
    twist: str = Field("", max_length=300)
    ending: str = Field("", max_length=300)
    genre: str = Field("", max_length=30)


class IdeaOptions(BaseModel):
    options: list[IdeaOption]


class IdeaExpanded(BaseModel):
    title: str
    synopsis: str


def _call(sv, system: str, user: str, schema, check):
    try:
        return sv.llm.generate(LLM_TASK, system, user, schema, check=check, step=STEP)
    except NeedsInput:
        raise JobError("「AI 写梗概」现在用的文本模型是手动粘贴，没法在向导里直接写。"
                       "到「设置 → 按步骤单独设置」给「AI 写梗概（新建剧集）」选一个文本模型") from None
    except JobError:
        raise
    except Exception as e:   # 模型连不上、输出一直不合格、命令超时……都给一句中文原因
        raise IdeaFailed(f"AI 出方案失败：{e}") from None


def _preset_ctx(style_preset: str) -> dict:
    p = get_preset(style_preset)
    return {"preset_key": p.key, "preset_label": p.label, "style_label": p.style_label}


def idea_options(sv, idea: str, *, target_seconds: float = 30.0, style_preset: str = "live_action",
                 feedback: str = "", exclude: list[str] | None = None, count: int = OPTION_COUNT) -> list[IdeaOption]:
    idea = (idea or "").strip()
    if not idea:
        raise JobError("先写一句点子，AI 才知道往哪个方向想")
    seen = [t.strip() for t in (exclude or []) if t and t.strip()][-MAX_EXCLUDE:]
    system, user = PromptLibrary().render(
        "idea_options", idea=idea, target_seconds=float(target_seconds), count=count,
        feedback=(feedback or "").strip(), exclude=seen, **_preset_ctx(style_preset))
    seen_set = {t.lower() for t in seen}

    def check(out: IdeaOptions) -> None:
        good = [o for o in out.options if o.title.strip() and o.logline.strip()]
        if len(good) < min(2, count):
            raise ValueError(f"要 {count} 个方向，每个都要有 title 和 logline，现在只有 {len(good)} 个能用")
        titles = [o.title.strip().lower() for o in good]
        if len(set(titles)) < len(titles):
            raise ValueError("有几个方向的 title 一样，每个方向要真的不一样")
        if seen_set and all(t in seen_set for t in titles):
            raise ValueError("出的方向和用户看过的一样，要换新的方向")

    out = _call(sv, system, user, IdeaOptions, check)
    return [o for o in out.options if o.title.strip() and o.logline.strip()][:count]


def expand_idea(sv, idea: str, options: list[IdeaOption], *, target_seconds: float = 30.0,
                style_preset: str = "live_action", current: str = "", instruction: str = "") -> IdeaExpanded:
    if not options:
        raise JobError("先选一个方向（也可以勾两个合在一起）")
    if len(options) > 2:
        raise JobError("最多合并两个方向，多了故事会乱")
    current, instruction = (current or "").strip(), (instruction or "").strip()
    lo, hi = length_range(target_seconds)
    system, user = PromptLibrary().render(
        "idea_expand", idea=(idea or "").strip(), options=[o.model_dump() for o in options],
        target_seconds=float(target_seconds), current=current, instruction=instruction,
        len_lo=lo, len_hi=hi, **_preset_ctx(style_preset))

    def check(out: IdeaExpanded) -> None:
        if not out.synopsis.strip():
            raise ValueError("synopsis 是空的，要写出梗概正文")
        if not out.title.strip():
            raise ValueError("title 是空的，要给片名")
        if current and out.synopsis.strip() == current:
            raise ValueError("梗概和原来的一字不差，没有按修改要求改")

    out = _call(sv, system, user, IdeaExpanded, check)
    return IdeaExpanded(title=out.title.strip()[:60], synopsis=out.synopsis.strip())


# ---------------------------------------------------------------- 继续出片
AUTOPILOT_STAGES = ["keyframes", "videos", "export"]


def submit_autopilot(sv, manager, episode_id: int, *, precheck=None) -> tuple[list[int], list[str]]:
    """分镜做完后一次排好：出镜角色（不是只出声的、还没有定妆照的）各出 1 张定妆照，首帧 → 视频 → 导出接在
    定妆照后面（depends_on）。返回 (任务 id, 跳过的说明)。"""
    from backend.domain.characters import portrait_of
    from backend.domain.models import Character, Episode
    from backend.domain.settings import portrait_engine
    from backend.engines.registry import check_ready
    from backend.pipeline.runs import RunOptions, submit_chain

    skipped: list[str] = []
    todo: list[tuple[int, str, str]] = []    # (角色 id, 名字, 引擎)
    with sv.db.session() as s:
        ep = s.get(Episode, episode_id)
        if ep is None:
            raise LookupError(f"找不到剧集 {episode_id}")
        if not ep.shots:
            raise JobError("这一集还没有分镜：等剧本、分镜跑完再点「继续出片」")
        proj = ep.project
        project_id = proj.id
        cast_ids: list[int] = []
        for sh in ep.shots:
            for c in sh.cast or []:
                cid = c.get("character_id") if isinstance(c, dict) else None
                if cid is not None and cid not in cast_ids:
                    cast_ids.append(cid)
        for cid in cast_ids:
            ch = s.get(Character, cid)
            if ch is None or ch.voice_only or portrait_of(s, cid) is not None:
                continue
            if not (ch.visual_prompt or "").strip():
                skipped.append(f"{ch.name} 还没有外貌提示词，没出定妆照（首帧只能按文字出脸）")
                continue
            todo.append((cid, ch.name, portrait_engine(sv, proj.image_engine, None,
                                                       preset=proj.style_preset or "")))
    ids: list[int] = []
    for cid, _name, engine in todo:
        eng = sv.engines.image(engine)
        check_ready(eng)
        ids.append(manager.submit("image.portrait", {"character_id": cid, "engine": engine},
                                  project_id=project_id, owner_type="character", owner_id=cid,
                                  model_family=eng.family))
    ids += submit_chain(manager, sv.db, sv.engines, episode_id, AUTOPILOT_STAGES, RunOptions(),
                        depends_on=list(ids), precheck=precheck)
    return ids, skipped
