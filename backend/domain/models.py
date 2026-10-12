"""SQLAlchemy 实体。字段含义见设计文档第 5 节。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class StyleBible(Base):
    __tablename__ = "style_bibles"
    id: Mapped[int] = mapped_column(primary_key=True)
    palette: Mapped[str] = mapped_column(Text, default="")
    lighting: Mapped[str] = mapped_column(Text, default="")
    texture: Mapped[str] = mapped_column(Text, default="")
    character_rules: Mapped[str] = mapped_column(Text, default="")
    mood_keywords: Mapped[list] = mapped_column(JSON, default=list)
    anchor_text: Mapped[str] = mapped_column(Text, default="")
    negative: Mapped[str] = mapped_column(Text, default="")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    aspect: Mapped[str] = mapped_column(String(8), default="9:16")
    image_engine: Mapped[str] = mapped_column(String(40), default="zimage")
    video_engine: Mapped[str] = mapped_column(String(40), default="h3_video")
    seed: Mapped[int] = mapped_column(Integer, default=12345)
    negative: Mapped[str] = mapped_column(Text, default="")
    # 风格预设（backend/domain/style_presets.py）：真人写实 / 3D 写实 / 日系动漫 / 国风漫画 / 美式漫画 / 商业广告
    style_preset: Mapped[str] = mapped_column(String(32), default="live_action",
                                              server_default="live_action")
    style_bible_id: Mapped[int | None] = mapped_column(
        ForeignKey("style_bibles.id", ondelete="SET NULL"), nullable=True)
    # 片尾卡：{"seconds": 3, "background": "#0b1a33", "lines": [{text, font, size, bold}]}
    end_card: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # AI 生成内容标识：{"label_text": "本片由 AI 辅助制作", "producer": "AI Studio"}；空 = 用默认值
    #（backend/post/aigc.py）。导出时角标烧进画面、出品方写进成片元数据
    aigc: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 镜头节奏：每镜几秒（3 / 4 / 5）；空 = 跟随视频引擎的甜区（default_seconds）
    shot_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 视频开头没接上首帧（出片自检 start_mismatch）时，同一个任务里换种子自动重拍一次
    auto_retry_video: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 固定时长视频引擎（Gemini 固定 10 秒、Muse 只认 3 / 5 / 10 秒）怎么用满一次生成：beats = 多拍镜头
    #（一次生成里切 2–4 拍，默认）/ group = 组镜拆分（第 2 步实现，现在按 off 处理）/ off = 一镜一生成（老行为）。
    # 分镜按它排；改了之后重跑分镜才生效（不动已有镜头）
    fixed_engine_pack: Mapped[str] = mapped_column(String(16), default="beats", server_default="beats")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    style_bible: Mapped[StyleBible | None] = relationship()
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Episode.number")
    characters: Mapped[list[Character]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Character.id")
    locations: Mapped[list[Location]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Location.id")
    props: Mapped[list[Prop]] = relationship(cascade="all, delete-orphan", order_by="Prop.id")


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    visual_prompt: Mapped[str] = mapped_column(Text, default="")
    core_features: Mapped[str] = mapped_column(Text, default="")
    voice: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # AI 推荐造型：[{label, visual_prompt, core_features}]，用户挑一个去抽卡或设为提示词
    design_options: Mapped[list] = mapped_column(JSON, default=list)
    # 三视图设定图用的全身描述（英文，从头到脚，LLM 按外貌提示词整理，可改）；空就用外貌提示词
    turnaround_look: Mapped[str] = mapped_column(Text, default="")
    # 只出声、不出镜（画外音、电话里的声音）：不抽定妆照，分镜角色表里不给外貌占位符
    voice_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    project: Mapped[Project] = relationship(back_populates="characters")
    variations: Mapped[list[Variation]] = relationship(cascade="all, delete-orphan",
                                                       order_by="Variation.id")


class Variation(Base):
    __tablename__ = "variations"
    id: Mapped[int] = mapped_column(primary_key=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    visual_prompt: Mapped[str] = mapped_column(Text, default="")


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    time_of_day: Mapped[str] = mapped_column(String(40), default="")
    atmosphere: Mapped[str] = mapped_column(Text, default="")
    visual_prompt: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="locations")


class Prop(Base):
    __tablename__ = "props"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    visual_prompt: Mapped[str] = mapped_column(Text, default="")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("project_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    target_seconds: Mapped[float] = mapped_column(Float, default=60.0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    stage_hashes: Mapped[dict] = mapped_column(JSON, default=dict)
    # 剧本体检（script.review）的 LLM 打分和改写建议，连同当时的输入指纹（分镜改了就标「已过期」）；没体检过是 null
    script_review: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    project: Mapped[Project] = relationship(back_populates="episodes")
    scene_blocks: Mapped[list[SceneBlock]] = relationship(
        cascade="all, delete-orphan", order_by="SceneBlock.order")
    shots: Mapped[list[Shot]] = relationship(cascade="all, delete-orphan", order_by="Shot.order")


class SceneBlock(Base):
    __tablename__ = "scene_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer, default=0)
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")


class Shot(Base):
    __tablename__ = "shots"
    # 槽位按 (owner_type, owner_id) 挂在镜头上、没有外键；id 不能复用，否则新镜头会继承旧镜头的候选。
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    scene_block_id: Mapped[int | None] = mapped_column(
        ForeignKey("scene_blocks.id", ondelete="SET NULL"), nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    code: Mapped[str] = mapped_column(String(16), default="")
    shot_size: Mapped[str] = mapped_column(String(40), default="")
    camera_move: Mapped[str] = mapped_column(String(80), default="")
    action: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[list] = mapped_column(JSON, default=list)
    cast: Mapped[list] = mapped_column(JSON, default=list)
    props: Mapped[list] = mapped_column(JSON, default=list)
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    motion_prompt: Mapped[str] = mapped_column(Text, default="")
    audio_prompt: Mapped[str] = mapped_column(Text, default="")
    image_engine: Mapped[str | None] = mapped_column(String(40), nullable=True)
    video_engine: Mapped[str | None] = mapped_column(String(40), nullable=True)
    duration: Mapped[float] = mapped_column(Float, default=5.0)
    use_end_frame: Mapped[bool] = mapped_column(Boolean, default=False)
    continue_from_prev: Mapped[bool] = mapped_column(Boolean, default=False)
    # 视频怎么出：first_frame = 先出首帧再图生视频；refs = 参考图直出（不出首帧，见 pipeline/videos.py）。
    # 空 = first_frame。开了接上一镜时一律按首帧模式（effective_video_mode）
    video_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 分镜建议这一镜接上一镜（同一场景、动作直接延续）：只是建议，工作台上一键打开 continue_from_prev
    suggest_continue: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 多拍镜头：一次生成里按时间戳切的 2–4 拍 [{start, end, shot_size, camera_move, action}]，首尾相接、
    # 覆盖整个镜头时长（domain/beats.py 校验）；空 = 普通镜头。台词仍在 dialogue（各句落在对应拍里）
    beats: Mapped[list | None] = mapped_column(JSON, nullable=True)
    transition_out: Mapped[str] = mapped_column(String(32), default="hard_cut")
    # llm：分镜生成的，重跑会被替换/清理；manual：用户手工加的，重跑分镜时要保留
    origin: Mapped[str] = mapped_column(String(16), default="llm")


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", "kind"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    selected_candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    candidates: Mapped[list[Candidate]] = relationship(cascade="all, delete-orphan",
                                                       order_by="Candidate.id")


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("slots.id", ondelete="CASCADE"))
    file_path: Mapped[str] = mapped_column(String(500))
    engine: Mapped[str] = mapped_column(String(40), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArchivedCandidate(Base):
    """历史候选：重跑分镜删掉镜头时，它槽位里的候选挪到这里（镜头行已经删了，所以带上镜头当时的快照），
    界面上可以恢复到同一集的别的镜头上。媒体文件一直在磁盘上。"""
    __tablename__ = "archived_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"),
                                            index=True)
    shot_code: Mapped[str] = mapped_column(String(16), default="")
    kind: Mapped[str] = mapped_column(String(32))
    file_path: Mapped[str] = mapped_column(String(500))
    engine: Mapped[str] = mapped_column(String(40), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    was_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    shot_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    archived_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(20))
    owner_id: Mapped[int] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(60), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    gpu: Mapped[bool] = mapped_column(Boolean, default=False)
    model_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    log: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class JobTrace(Base):
    """任务的调用明细：每次 LLM 调用、引擎提交、ffmpeg 命令一条（见 backend/queue/trace.py）。"""
    __tablename__ = "job_traces"
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_job_traces_job_seq"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))          # llm / engine / ffmpeg / note
    title: Mapped[str] = mapped_column(String(200), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BrowserConversation(Base):
    """网页会话记录：同一个作用域（角色 / 镜头 / 场景 / 道具 / 某集某一步）在同一个网站上用的那条网页对话。
    重新生成时接着这条对话发（见 backend/engines/browser/conversations.py）。"""
    __tablename__ = "browser_conversations"
    __table_args__ = (UniqueConstraint("site", "scope_kind", "scope_id", name="uq_browser_conv_scope"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(String(20))                 # chatgpt / gemini / claude
    scope_kind: Mapped[str] = mapped_column(String(20))           # character / shot / location / prop / episode
    scope_id: Mapped[str] = mapped_column(String(120))            # 角色 id；episode 是「集 id:步骤」
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(500), default="")
    conversation_id: Mapped[str] = mapped_column(String(120), default="")
    turns: Mapped[int] = mapped_column(Integer, default=0)        # 在这条对话里发过几次请求
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    variant: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(String(500))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """全局设置（设置页「默认模型」）：key → JSON 值。值为 null 或没有这一行就是「跟随默认」。"""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
