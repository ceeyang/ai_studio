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
    style_bible_id: Mapped[int | None] = mapped_column(
        ForeignKey("style_bibles.id", ondelete="SET NULL"), nullable=True)
    # 片尾卡：{"seconds": 3, "background": "#0b1a33", "lines": [{text, font, size, bold}]}
    end_card: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"))
    variant: Mapped[str] = mapped_column(String(16))
    file_path: Mapped[str] = mapped_column(String(500))
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
