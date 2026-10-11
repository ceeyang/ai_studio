"""HTTP API 的输入输出模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.llm.schemas import Transition
from backend.post.options import ExportOptions

Aspect = Literal["9:16", "16:9"]
# 镜头视频怎么出：first_frame = 先出首帧再图生视频；refs = 参考图直出（不出首帧）
VideoMode = Literal["first_frame", "refs"]
Stage = Literal["script", "storyboard", "keyframes", "videos", "export", "all", "fit_khmer"]
NextStage = Literal["keyframes", "videos", "export"]
# 风格预设，和 backend/domain/style_presets.py 的 PRESETS 一致（有测试对齐）
StylePresetKey = Literal["live_action", "cg_realistic", "anime", "guofeng", "western_comic",
                         "commercial"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


AssetKindKey = Literal["character", "location", "prop"]


class ProjectImportFrom(BaseModel):
    """新建项目时「从已有项目导入资产」（续季）：源项目里这几类资产全部复制过来。"""
    source: str                                       # 源项目 slug
    kinds: list[AssetKindKey] = ["character", "location", "prop"]
    with_style: bool = True                           # 沿用源项目的风格设定（复制一份）
    candidates: Literal["selected", "all"] = "selected"


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    slug: str | None = None
    aspect: Aspect = "9:16"
    image_engine: str | None = None     # 不给就按设置页：首帧这一步 → 全局默认图片来源 → zimage
    video_engine: str | None = None     # 不给就按设置页：视频这一步 → 全局默认视频来源 → h3_video
    seed: int = 12345
    negative: str | None = None         # 不给就用风格预设的默认负面词
    style_preset: StylePresetKey = "live_action"
    import_from: ProjectImportFrom | None = None   # 续季：建好后从已有项目导入资产


class ProjectPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    aspect: Aspect | None = None
    # 改预设：负面词还是旧预设的默认值（或空）时跟着换；已有的风格锚和外观要重新生成视觉设定才变
    style_preset: StylePresetKey | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    seed: int | None = None
    negative: str | None = None
    archived: bool | None = None
    # 镜头节奏：每镜几秒；0 = 跟随视频引擎（清空）
    shot_seconds: Literal[0, 3, 4, 5] | None = None
    auto_retry_video: bool | None = None     # 视频开头没接上首帧时自动重拍一次


class ProjectOut(ORM):
    id: int
    slug: str
    title: str
    aspect: str
    image_engine: str
    video_engine: str
    seed: int
    negative: str
    style_preset: str = "live_action"
    style_bible_id: int | None
    shot_seconds: float | None = None
    auto_retry_video: bool = False
    archived: bool
    created_at: datetime


class StylePresetOut(BaseModel):
    key: str
    label: str
    description: str
    negative: str
    image_engines: list[str]
    video_engines: list[str]
    portrait_engines: list[str] = []
    has_storyboard_guidance: bool = False
    image_engine: str | None = None     # 推荐引擎里第一个可用的（只是建议，新建项目时预选）
    video_engine: str | None = None
    portrait_engine: str | None = None  # 定妆照推荐引擎里第一个可用的（设置页「角色定妆照」这一步）


class StyleIO(ORM):
    anchor_text: str = ""
    palette: str = ""
    lighting: str = ""
    texture: str = ""
    character_rules: str = ""
    mood_keywords: list[str] = Field(default_factory=list)
    negative: str = ""


class AppearanceRefOut(BaseModel):
    """「从参考图写外观」上传的参考照片，和 LLM 按它写好、等用户确认的外观。"""
    candidate_id: int
    url: str
    visual_prompt: str | None = None     # LLM 写好的外观（还没写 / 用户放弃了是 None）
    core_features: str | None = None


class CharacterOut(ORM):
    id: int
    project_id: int
    name: str
    description: str
    visual_prompt: str
    core_features: str
    voice: str | None
    voice_only: bool = False             # 只出声、不出镜：不抽定妆照，分镜里不给外貌占位符
    portrait_candidate_id: int | None = None
    portrait_url: str | None = None
    portraits: list[CandidateOut] = Field(default_factory=list)   # 全部定妆照候选（上传的 + AI 抽的）
    design_options: list[dict] = Field(default_factory=list)      # AI 推荐造型
    active_jobs: list[JobBrief] = Field(default_factory=list)     # 进行中的抽卡 / 推荐造型 / 三视图任务
    last_failure: JobBrief | None = None    # 最近失败的抽卡（之后没有成功的抽卡才给）
    turnaround: TurnaroundOut | None = None                       # 三视图设定图和切出来的格子
    appearance_ref: AppearanceRefOut | None = None                # 外观参考照片（不当出图参考图用）


class JobBrief(BaseModel):
    id: int
    type: str
    status: str
    sheet: str | None = None             # image.turnaround 任务出的是哪种设定图
    progress: float | None = None        # 0-1，运行中且处理器报过进度才有
    error: str | None = None             # 失败任务才有：错误第一行，最多 200 字
    finished_at: datetime | None = None


class JobContext(BaseModel):
    """任务是给谁做的：任务页显示「项目 · 第1集 · S05」，点了跳到对应页面。"""
    label: str
    url: str | None = None
    project_slug: str | None = None
    episode_id: int | None = None
    shot_id: int | None = None
    character_id: int | None = None


class JobOut(BaseModel):
    id: int
    type: str
    status: str
    project_id: int | None
    owner_type: str | None
    owner_id: int | None
    payload: dict
    gpu: bool
    model_family: str | None
    priority: int
    depends_on: list
    log: str
    error: str | None
    result: dict | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    context: JobContext | None = None


class TurnaroundSheetOut(BaseModel):
    sheet: str                           # body：全身 4 视角 / face：头像 3 视角
    label: str
    views: list[str]                     # 从左到右切出来的视角
    selected_candidate_id: int | None
    url: str | None
    stale: bool                          # 选定的这张是按旧定妆照 / 旧全身描述出的
    crop_method: str | None              # segment 按人物切 / equal 等宽切 / None 还没切
    candidates: list[CandidateOut]


class TurnaroundCellOut(BaseModel):
    view: str
    label: str
    sheet: str
    selected_candidate_id: int | None
    url: str | None
    stale: bool                          # 切自的设定图过期了


class TurnaroundOut(BaseModel):
    look: str                            # 全身描述（英文），空表示用外貌提示词
    default_engine: str
    sheets: list[TurnaroundSheetOut]
    cells: list[TurnaroundCellOut]


class TurnaroundLookPatch(BaseModel):
    look: str


class TurnaroundGenerateIn(BaseModel):
    sheets: list[Literal["body", "face"]] | None = None   # 不给就全身 + 头像各一张
    count: int = Field(1, ge=1, le=4)                     # 每种出几张候选
    engine: str | None = None                             # 不给用默认（H3 + 定妆照锁脸）
    new_conversation: bool = False                        # 网页引擎：这次不续接之前的网页会话，新开一条


class TurnaroundSelectIn(BaseModel):
    candidate_id: int


class TurnaroundCropIn(BaseModel):
    method: Literal["auto", "equal"] = "auto"


class PortraitDrawIn(BaseModel):
    count: int = Field(1, ge=1, le=4)
    visual_prompt: str | None = None     # 不给就用角色当前的外貌提示词
    engine: str | None = None            # 不给用项目的图像引擎
    new_conversation: bool = False                        # 网页引擎：这次不续接之前的网页会话，新开一条


class SuggestIn(BaseModel):
    count: int = Field(3, ge=1, le=6)
    episode_id: int | None = None        # 读哪一集的剧本；不给读全部剧集


class RecommendIn(BaseModel):
    count: int = Field(3, ge=1, le=6)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    visual_prompt: str = ""
    core_features: str = ""
    voice_only: bool = False


class PortraitPathIn(BaseModel):
    path: str = Field(min_length=1)


class RefreshLooksIn(BaseModel):
    old: list[str] = Field(default_factory=list)   # 刚换掉的旧外貌（不给就只用定妆照候选、推荐方案里记着的）


class RefreshLooksOut(BaseModel):
    updated: list[str]        # 换成了占位符的镜头（「第1集 S03」）
    replacements: int
    already: list[str]        # 本来就用占位符的镜头（自动跟着当前造型）
    unmatched: list[str]      # 出场但找不到一字不差的旧外貌的镜头（分镜改写过，要手改或重写分镜）
    partial: list[str] = Field(default_factory=list)   # 只对上一部分、换了还会留下旧外貌的镜头（没换）
    blocks_rebased: int = 0   # 跟着更新了分镜记录的段落数（不再要求删掉重出）


class CharacterPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    visual_prompt: str | None = None
    core_features: str | None = None
    voice: str | None = None
    voice_only: bool | None = None


class LocationOut(ORM):
    id: int
    project_id: int
    name: str
    time_of_day: str
    atmosphere: str
    visual_prompt: str
    # 场景图（不出人物的环境参考图，首帧参考图里排第一）：选定的那张和全部候选
    image_candidate_id: int | None = None
    image_url: str | None = None
    image_stale: bool = False            # 选定的场景图是按旧设定（名字 / 时段 / 画面提示词）出的
    images: list[CandidateOut] = Field(default_factory=list)
    active_jobs: list[JobBrief] = Field(default_factory=list)   # 排队 / 生成中的场景图任务
    last_failure: JobBrief | None = None                        # 最近一次出场景图失败（之后没出过新的）


class LocationDrawIn(BaseModel):
    count: int = Field(2, ge=1, le=4)
    engine: str | None = None
    new_conversation: bool = False                        # 网页引擎：这次不续接之前的网页会话，新开一条


class LocationPatch(BaseModel):
    name: str | None = None
    time_of_day: str | None = None
    atmosphere: str | None = None
    visual_prompt: str | None = None


class PropUse(BaseModel):
    episode_number: int
    shot_code: str


class PropOut(ORM):
    """道具（分镜里镜头 props 的英文说法）和它的道具图（只拍道具、白底，首帧参考图里排在角色后面）。"""
    id: int
    project_id: int
    name: str
    description: str
    visual_prompt: str
    image_candidate_id: int | None = None
    image_url: str | None = None
    image_stale: bool = False
    images: list[CandidateOut] = Field(default_factory=list)
    active_jobs: list[JobBrief] = Field(default_factory=list)
    last_failure: JobBrief | None = None
    uses: list[PropUse] = Field(default_factory=list)       # 哪几集的哪几个镜头用到


class PropPatch(BaseModel):
    description: str | None = None
    visual_prompt: str | None = None


class AssetOut(BaseModel):
    """资产选择器的一项：本项目 / 本集已经有的图片或视频。id 是 "cand-<候选 id>" 或 "arch-<历史候选 id>"。"""
    id: str
    kind: str
    slot_kind: str
    media: str
    label: str
    source: str
    url: str
    thumb_url: str
    width: int | None = None
    height: int | None = None
    selected: bool = False
    shot_id: int | None = None
    owner_id: int | None = None
    engine: str = ""
    created_at: datetime | None = None


class AssetPick(BaseModel):
    id: str
    # 视频资产取哪一帧：first / last
    frame: str | None = Field(None, pattern="^(first|last)$")


class ShotRefsFromAssetsIn(BaseModel):
    assets: list[AssetPick] = Field(min_length=1, max_length=2)


class FirstFrameFromAssetIn(AssetPick):
    crop: bool = False       # 画幅和项目不一样时居中裁成项目画幅
    select: bool = True


class EpisodeIn(BaseModel):
    title: str = ""
    source_text: str = ""
    target_seconds: float = Field(60.0, gt=0, le=3600)
    number: int | None = Field(None, ge=1)


class EpisodeIdeaIn(BaseModel):
    """新建剧集对话框里的「AI 写梗概 / AI 续写」：框里已有的文字作为点子或开头。"""
    title: str = Field("", max_length=200)
    target_seconds: float = Field(30.0, gt=0, le=3600)
    number: int | None = Field(None, ge=1)
    text: str = Field("", max_length=20000)


class EpisodeIdeaOut(BaseModel):
    text: str
    mode: Literal["new", "expand", "continue"]


# ---- 剧本体检：规则检查现算；LLM 打分存在 episodes.script_review
class ReviewFindingOut(BaseModel):
    key: str
    severity: Literal["ok", "info", "warn", "error"]
    title: str
    detail: str = ""
    shots: list[str] = Field(default_factory=list)


class ReviewRulesOut(BaseModel):
    total_seconds: float
    shot_count: int
    target_seconds: float
    findings: list[ReviewFindingOut]


class ReviewScoreOut(BaseModel):
    score: int
    reason: str


class ReviewSuggestionOut(BaseModel):
    dimension: Literal["hook", "conflict", "ending", "pacing"]
    ref: str
    problem: str = ""
    rewrite: str


class ScriptReviewLLMOut(BaseModel):
    hook: ReviewScoreOut
    conflict: ReviewScoreOut
    ending: ReviewScoreOut
    pacing: ReviewScoreOut
    summary: str = ""
    suggestions: list[ReviewSuggestionOut] = Field(default_factory=list)
    created_at: str | None = None
    job_id: int | None = None
    stale: bool = False             # 体检之后分镜（时长、动作、台词）或段落改过了


class ReviewJobOut(BaseModel):
    id: int
    status: str
    error: str | None = None


class ScriptReviewOut(BaseModel):
    episode_id: int
    rules: ReviewRulesOut
    review: ScriptReviewLLMOut | None = None
    job: ReviewJobOut | None = None   # 最近一次体检任务（排队中 / 运行中 / 失败时界面要显示）


class EpisodePatch(BaseModel):
    title: str | None = None
    source_text: str | None = None
    target_seconds: float | None = Field(None, gt=0, le=3600)


class EpisodeOut(ORM):
    id: int
    project_id: int
    number: int
    title: str
    source_text: str
    target_seconds: float
    status: str


class EpisodeStats(BaseModel):
    id: int
    number: int
    title: str
    status: str
    shot_count: int
    export_count: int
    cover_url: str | None


class ProjectOverview(BaseModel):
    episode_count: int
    cover_url: str | None
    episodes: list[EpisodeStats]


class SceneBlockOut(ORM):
    id: int
    order: int
    location_id: int | None
    text: str


class SceneBlockPatch(BaseModel):
    text: str | None = None
    location_id: int | None = None


class CandidateOut(ORM):
    id: int
    slot_id: int
    file_path: str
    url: str = ""
    engine: str
    params: dict
    input_hash: str
    job_id: int | None
    note: str
    rating: int | None
    created_at: datetime


class ArchivedCandidateOut(ORM):
    """历史候选：重跑分镜删掉的镜头留下的首帧/视频候选。"""
    id: int
    episode_id: int
    shot_code: str
    kind: str
    file_path: str
    url: str = ""
    engine: str
    params: dict
    note: str
    was_selected: bool
    shot_snapshot: dict
    reason: str
    created_at: datetime
    archived_at: datetime


class RestoreArchivedIn(BaseModel):
    shot_id: int


class ExportOut(ORM):
    id: int
    episode_id: int
    variant: str
    file_path: str
    url: str = ""
    settings: dict
    label: str = ""           # 可读标签，拼法照抄前端：画幅 · 字幕 · 配音 · 原声底 · 对齐 · 片尾卡 · 时长
    is_final: bool = False
    created_at: datetime


class BgmOut(BaseModel):
    """剧集配乐（GET/POST /api/episodes/{id}/bgm）。"""
    candidate_id: int
    url: str
    filename: str
    created_at: datetime


class ExportPatch(BaseModel):
    is_final: bool


class SlotOut(BaseModel):
    id: int
    kind: str
    selected_candidate_id: int | None
    stale: bool = False
    stale_reason: str | None = None      # 过期原因：「陈师傅 定妆照换了」「提示词改了」……
    new_candidates: int = 0              # 比选定的那张更新、还没选的候选数
    candidates: list[CandidateOut] = Field(default_factory=list)


class DialogueIn(BaseModel):
    """一句台词。km 是高棉语字幕/配音稿；km_review：ai_draft = AI 译稿待母语校对，checked = 已校对。"""
    model_config = ConfigDict(extra="allow")
    speaker: str = ""
    zh: str = ""
    start: float = Field(0.0, ge=0)
    end: float = Field(0.0, ge=0)
    km: str | None = None
    km_review: Literal["ai_draft", "checked"] | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.end < self.start:
            raise ValueError(f"台词「{self.zh}」的 end 不能早于 start")
        return self


class ShotFields(BaseModel):
    shot_size: str | None = None
    camera_move: str | None = None
    action: str | None = None
    dialogue: list[DialogueIn] | None = None
    cast: list[dict] | None = None
    props: list | None = None
    image_prompt: str | None = None
    motion_prompt: str | None = None
    audio_prompt: str | None = None
    image_engine: str | None = None
    video_engine: str | None = None
    duration: float | None = Field(None, gt=0, le=30)
    use_end_frame: bool | None = None
    continue_from_prev: bool | None = None
    video_mode: VideoMode | None = None      # null = 首帧模式（和 first_frame 一样）
    suggest_continue: bool | None = None     # false = 不要这条「建议接上一镜」
    transition_out: Transition | None = None


class ShotIn(ShotFields):
    after_shot_id: int | None = None


class ShotOut(ORM):
    id: int
    episode_id: int
    scene_block_id: int | None
    order: int
    code: str
    shot_size: str
    camera_move: str
    action: str
    dialogue: list
    cast: list
    props: list
    image_prompt: str
    motion_prompt: str
    audio_prompt: str
    image_engine: str | None
    video_engine: str | None
    duration: float
    use_end_frame: bool
    continue_from_prev: bool
    video_mode: str | None = None    # first_frame / refs（参考图直出）；空 = first_frame
    suggest_continue: bool = False   # 分镜建议这一镜接上一镜（只是建议，用户一键打开）
    transition_out: str
    origin: str = "llm"        # llm = 分镜生成的；manual = 手工添加（含拆分出来的）


class ContinuePrevOut(BaseModel):
    shot_id: int
    code: str
    has_video: bool


class VideoRefOut(BaseModel):
    """参考图直出时这一镜视频要带的一张参考图（场景图 / 构图参考 / 定妆照 / 三视图 / 道具图）。"""
    candidate_id: int
    label: str
    kind: str = ""
    url: str
    used: bool = True    # false = 超出视频引擎的参考图上限，这张带不上


class ShotDetail(ShotOut):
    slots: list[SlotOut] = Field(default_factory=list)
    active_jobs: list[JobBrief] = Field(default_factory=list)   # 排队 / 运行 / 等输入的首帧、视频任务
    failures: list[JobBrief] = Field(default_factory=list)      # 首帧、视频各自最近一次失败（之后没出过新的才给）
    # 首帧引擎完全不吃参考图（max_refs=0）时，这个镜头本该带上却用不上的参考图（如「老周 定妆照」）
    first_frame_refs_unused: list[str] = Field(default_factory=list)
    # 首帧引擎的参考图上限装不下所有出场角色的定妆照时，没带上的那几张（「小李 定妆照」）
    first_frame_portraits_dropped: list[str] = Field(default_factory=list)
    # image_prompt 里有外貌占位符 {{char:<id>}} 时，出图实际用的文字（占位符按景别换成角色当前外貌）
    image_prompt_resolved: str | None = None
    # image_prompt 里出现的外貌占位符 → 角色名（「{{char:3}}」→「老周」），镜头抽屉显示图例
    placeholder_names: dict[str, str] = Field(default_factory=dict)
    # 镜头构图参考图（最多 2 张，首帧参考图里排在场景图后面）
    shot_refs: list[CandidateOut] = Field(default_factory=list)
    # 首帧引擎最多吃几张参考图（0 = 不吃参考图，构图参考、场景图都用不上）；引擎不存在时为空
    image_max_refs: int | None = None
    # 开了「接上一镜」时：上一镜是哪个、有没有选定视频（没有就「等上一镜视频」）；第一镜或没开时为空
    continue_prev: ContinuePrevOut | None = None
    # 参考图直出（video_mode = refs 且没开接上一镜）时视频要带的参考图，按发给引擎的顺序；首帧模式为空
    video_refs: list[VideoRefOut] = Field(default_factory=list)


class EpisodeDetail(EpisodeOut):
    scene_blocks: list[SceneBlockOut] = Field(default_factory=list)
    shots: list[ShotDetail] = Field(default_factory=list)


class ReorderIn(BaseModel):
    shot_ids: list[int]


class RunIn(BaseModel):
    stage: Stage
    shots: list[str] | None = None
    candidates: int = Field(1, ge=1, le=4)
    force: bool = False
    priority: int = 0
    export: ExportOptions | None = None   # 只对 export 阶段有效：字幕 / 配音 / 片尾卡 / 裁尾
    then: list[NextStage] | None = None   # 本阶段完成后接着跑，如 fit_khmer → videos → export
    scene_block_ids: list[int] | None = None   # 只重写这些段落的分镜（只对 storyboard 有效）
    note: str | None = Field(None, max_length=500)   # 修改意见，写进提示词；非空时等同 force
    # 不强制的剧本/分镜运行会删掉有选定首帧/视频或改过的镜头时，要带 confirm=true 才提交
    confirm: bool = False
    new_conversation: bool = False                        # 网页引擎：这次不续接之前的网页会话，新开一条


class FitKhmerIn(BaseModel):
    """按高棉语时长改写中文；then 可接 videos / export（export 用 export 选项）。"""
    shot_ids: list[int] | None = None
    force: bool = False
    then: list[NextStage] = Field(default_factory=list)
    export: ExportOptions | None = None


class TranslateIn(BaseModel):
    force: bool = False                                   # 已有译文的也重翻
    glossary: dict[str, str] = Field(default_factory=dict)  # 固定译名，如店名


class EndCardLine(BaseModel):
    text: str = Field("", max_length=200)
    font: str | None = Field(None, max_length=100)        # 留空用 subtitles.latin_font
    size: int = Field(40, ge=8, le=240)                   # 1080×1920 画面上的像素字号
    bold: bool = False


class EndCardIO(BaseModel):
    seconds: float = Field(3.0, ge=1, le=10)
    background: str = Field("#0b1a33", pattern=r"^#[0-9a-fA-F]{6}$")
    lines: list[EndCardLine] = Field(default_factory=list, max_length=16)


class RunOut(BaseModel):
    job_ids: list[int]
    warnings: list[str] = Field(default_factory=list)   # 不拦提交的提醒（如出场角色还没有定妆照）
    notes: list[str] = Field(default_factory=list)      # 说明（如接上一镜的镜头等上一镜视频，这次跳过）


class RerunImpact(BaseModel):
    stage: str                 # script / storyboard / all
    will_run: bool             # False = 输入没变、没勾强制，会整体跳过
    blocks: list[int]          # 会重跑的段落序号（从 1 开始）
    shots_deleted: int         # 会被删掉的镜头数（只算 origin=llm）
    selected_first_frames: int  # 其中有选定首帧的
    selected_videos: int       # 其中有选定视频的
    manual_kept: int           # 手工镜头数（保留）
    overwrites: list[str]      # script 阶段会覆盖的东西，如 ["角色描述", "场景氛围"]
    edited_shots: int = 0      # 会被删掉的镜头里，生成后被改过的（改了提示词、台词、拆分出来的……）
    rules_outdated: bool = False  # 分镜规则更新过，现有镜头是按旧规则出的（不强制不会自动重跑）


class CandidatePatch(BaseModel):
    note: str | None = None
    rating: int | None = Field(None, ge=0, le=5)


class RetryIn(BaseModel):
    payload: dict | None = None


class InputIn(BaseModel):
    text: str = Field(min_length=1)



class KindTakesOut(ORM):
    """一个镜头一个槽位（首帧 / 视频）的抽卡数。"""
    takes: int                          # 成功的抽（候选数）
    failed: int                         # 失败的任务数
    selected_index: int | None          # 选定的是第几条（从 1 开始）；没选定为 null
    engines: dict[str, int]             # 引擎 → 成功抽数


class ShotTakesOut(ORM):
    shot_id: int
    code: str
    first_frame: KindTakesOut
    video: KindTakesOut


class EngineTakesOut(ORM):
    engine: str
    kind: str                           # first_frame / video
    takes: int
    failed: int


class TakeStatsOut(ORM):
    """抽卡统计（GET /api/episodes/{id}/take-stats）：见 backend/domain/take_stats.py。"""
    episode_id: int
    shots: list[ShotTakesOut]
    total_takes: int
    failed: int
    first_frame_takes: int
    video_takes: int
    shots_with_video: int
    usable_rate: float | None
    avg_takes_per_finished_shot: float | None
    engines: list[EngineTakesOut]
    archived_takes: dict[str, int]      # 废弃镜头（重跑分镜删掉的）的抽卡：kind → 条数


class AssetImportIn(BaseModel):
    """从别的项目导入角色 / 场景 / 道具。all=True 时忽略各 *_ids，三类（或 kinds 里的）全部导入。"""
    source: str                                       # 源项目 slug
    character_ids: list[int] | None = None
    location_ids: list[int] | None = None
    prop_ids: list[int] | None = None
    all: bool = False
    kinds: list[AssetKindKey] | None = None
    on_conflict: Literal["skip", "rename"] = "skip"
    rename_suffix: str = Field("（续）", max_length=20)
    candidates: Literal["selected", "all"] = "selected"
    with_style: bool = False                          # 沿用源项目的风格设定（复制一份，覆盖本项目的）


class ImportedAssetOut(BaseModel):
    kind: AssetKindKey
    source_id: int
    id: int
    name: str
    renamed_from: str | None = None
    files: int = 0


class SkippedAssetOut(BaseModel):
    kind: AssetKindKey
    source_id: int
    name: str
    reason: str


class AssetImportOut(BaseModel):
    imported: list[ImportedAssetOut] = []
    skipped: list[SkippedAssetOut] = []
    warnings: list[str] = []
    files_copied: int = 0
    style_copied: bool = False


class AssetSummaryItem(BaseModel):
    id: int
    name: str
    detail: str = ""                 # 角色的简介 / 场景的时段 / 道具的补充描述
    thumb_url: str | None = None     # 选定的定妆照 / 场景图 / 道具图
    image_count: int = 0             # 这个资产各槽位里的候选总数（选「全部候选」时会复制这么多张）


class AssetSummaryOut(BaseModel):
    characters: list[AssetSummaryItem] = []
    locations: list[AssetSummaryItem] = []
    props: list[AssetSummaryItem] = []
    has_style: bool = False


TurnaroundSheetOut.model_rebuild()
TurnaroundOut.model_rebuild()
CharacterOut.model_rebuild()  # portraits / turnaround 引用了后面定义的 CandidateOut
LocationOut.model_rebuild()  # images 引用了后面定义的 CandidateOut


# ---------------------------------------------------------------- 从一个点子出片（首页向导）
class IdeaOptionIO(BaseModel):
    title: str = Field(max_length=60)
    logline: str = Field(max_length=400)
    hook: str = Field("", max_length=300)
    twist: str = Field("", max_length=300)
    ending: str = Field("", max_length=300)
    genre: str = Field("", max_length=30)


class IdeaOptionsIn(BaseModel):
    idea: str = Field(min_length=1, max_length=2000)
    target_seconds: float = Field(30.0, gt=0, le=600)
    style_preset: StylePresetKey = "live_action"
    feedback: str = Field("", max_length=500)          # 「要更搞笑 / 要反转」之类，重出时用
    exclude: list[str] = Field(default_factory=list)   # 「再来几个」：看过的方向标题
    count: int = Field(4, ge=2, le=6)


class IdeaOptionsOut(BaseModel):
    options: list[IdeaOptionIO]


class IdeaExpandIn(BaseModel):
    idea: str = Field("", max_length=2000)
    options: list[IdeaOptionIO] = Field(min_length=1, max_length=2)   # 一个，或两个合并
    target_seconds: float = Field(30.0, gt=0, le=600)
    style_preset: StylePresetKey = "live_action"
    current: str = Field("", max_length=20000)          # 现在的梗概（按修改要求改写时带上）
    instruction: str = Field("", max_length=500)        # 修改要求


class IdeaExpandOut(BaseModel):
    title: str
    synopsis: str


class IdeaStartIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(min_length=1, max_length=20000)
    target_seconds: float = Field(30.0, gt=0, le=600)
    aspect: Aspect = "9:16"
    style_preset: StylePresetKey = "live_action"
    image_engine: str | None = None     # 不给就按风格预设推荐（和新建项目一样）
    video_engine: str | None = None


class IdeaStartOut(BaseModel):
    slug: str
    episode_id: int
    episode_number: int
    job_ids: list[int]


class AutopilotOut(BaseModel):
    job_ids: list[int]
    skipped: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
