"""全局设置：默认文本模型、新项目的图片/视频来源，以及按步骤单独设置。

存在 app_settings 表里（key → JSON），每次调用时现读（SQLite 一次主键查询），改了不用重启。
值为 null / 没有这一行 = 跟随默认。

规则：设置页只管默认，项目和镜头上选的总是优先。生效顺序（越靠前越优先）：
- 文本模型（LLM provider）：这一步的单独设置 → 全局默认文本模型 → config.yaml（llm.routes[任务] → llm.default）
- 首帧 / 视频：镜头上单独选的引擎 → 项目的引擎。设置页的「新项目的图片/视频来源」只在新建项目时
  用来填项目引擎（不给就 zimage / h3_video），不影响已有项目。
- 定妆照 / 三视图：这次手选的引擎 → 设置页这一步的单独设置 → （三视图：有 H3 用 H3）→ 项目的图像引擎。
  这两步没有项目级的选择，项目的图像引擎就是它们的「默认」，所以这一步的设置排在它前面。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.domain.models import AppSetting, utcnow

# 用到 LLM 的步骤：key 就是 LLMGateway.generate 的 step（默认等于任务名）
LLM_STEPS: dict[str, str] = {
    "script": "剧本",
    "storyboard": "分镜",
    "character.suggest": "推荐造型",
    "character.recommend": "推荐角色",
    "character.turnaround_look": "三视图全身描述",
    "dialogue.fit_khmer": "高棉语台词适配",
    "subtitles.translate": "字幕翻译（配音前）",
}
# 首帧 / 视频没有按步骤设置：它们跟项目和镜头走（项目引擎就是它们的设置）
IMAGE_STEPS: dict[str, str] = {"portrait": "角色定妆照", "turnaround": "三视图"}
VIDEO_STEPS: dict[str, str] = {}
STEP_KIND = {**{k: "llm" for k in LLM_STEPS}, **{k: "image" for k in IMAGE_STEPS},
             **{k: "video" for k in VIDEO_STEPS}}
STEP_LABEL = {**LLM_STEPS, **IMAGE_STEPS, **VIDEO_STEPS}
GLOBAL_KEYS = {"text_model": "llm", "image_engine": "image", "video_engine": "video"}
FALLBACK_IMAGE = "zimage"
FALLBACK_VIDEO = "h3_video"
SHEET_ENGINE = "h3_image"          # 三视图没设置时优先用 H3（带定妆照锁脸）


@dataclass
class AppSettings:
    text_model: str | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    steps: dict[str, str] = field(default_factory=dict)    # 只放设置了的步骤

    def step(self, key: str) -> str | None:
        return self.steps.get(key) or None


class SettingsStore:
    def __init__(self, db):
        self.db = db

    def load(self) -> AppSettings:
        with self.db.session() as s:
            rows = {r.key: r.value for r in s.scalars(select(AppSetting))}
        st = AppSettings(**{k: (rows.get(k) or None) for k in GLOBAL_KEYS})
        st.steps = {k[5:]: v for k, v in rows.items()
                    if k.startswith("step:") and v and k[5:] in STEP_KIND}
        return st

    def save(self, values: dict[str, Any]) -> None:
        """values：text_model / image_engine / video_engine / step:<key> → 值（None = 跟随默认）。"""
        # 两个请求同时写同一个新 key 时不能撞主键：用 SQLite 的 INSERT … ON CONFLICT DO UPDATE
        with self.db.session() as s:
            for key, val in values.items():
                if val in (None, ""):
                    s.execute(delete(AppSetting).where(AppSetting.key == key))
                    continue
                now = utcnow()
                stmt = sqlite_insert(AppSetting).values(key=key, value=val, updated_at=now)
                s.execute(stmt.on_conflict_do_update(index_elements=[AppSetting.key],
                                                     set_={"value": val, "updated_at": now}))

    # ------------------------------------------------------------ LLM
    def llm_provider(self, step: str) -> str | None:
        """这一步该用的 provider id；None = 按 config.yaml。给 LLMGateway 在调用时问。"""
        st = self.load()
        return st.step(step) or st.text_model


def _settings(sv) -> AppSettings:
    store = getattr(sv, "settings", None)
    return store.load() if store is not None else AppSettings()


def _first(names: list[str], *cands: str | None) -> str | None:
    for c in cands:
        if c and c in names:
            return c
    return None


def default_image_engine(sv, st: AppSettings | None = None) -> str:
    """新建项目的图像引擎：设置页「新项目的图片来源」→ zimage → 第一个可用的（设置里的引擎停用了就跳过）。"""
    st = st or _settings(sv)
    names = sv.engines.names("image")
    return _first(names, st.image_engine, FALLBACK_IMAGE) or (names[0] if names else FALLBACK_IMAGE)


def default_video_engine(sv, st: AppSettings | None = None) -> str:
    """新建项目的视频引擎：设置页「新项目的视频来源」→ h3_video → 第一个可用的。"""
    st = st or _settings(sv)
    names = sv.engines.names("video")
    return _first(names, st.video_engine, FALLBACK_VIDEO) or (names[0] if names else FALLBACK_VIDEO)


def portrait_engine(sv, project_engine: str, explicit: str | None = None,
                    st: AppSettings | None = None) -> str:
    """定妆照：明确指定 → 定妆照这一步的设置 → 项目的图像引擎。"""
    if explicit:
        return explicit
    names = sv.engines.names("image")
    return _first(names, (st or _settings(sv)).step("portrait")) or project_engine


def sheet_engine(sv, project_engine: str, explicit: str | None = None,
                 st: AppSettings | None = None) -> str:
    """三视图：明确指定 → 三视图这一步的设置 → H3（有就用）→ 项目的图像引擎。"""
    if explicit:
        return explicit
    names = sv.engines.names("image")
    return _first(names, (st or _settings(sv)).step("turnaround"), SHEET_ENGINE) or project_engine
