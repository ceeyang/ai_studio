"""道具图：分镜里每个镜头的 props 是英文说法（「a dented red thermos with a chipped lid」），同一个道具在
整个项目里逐字沿用同一个说法（见 prompts/storyboard.md）。这里按说法给道具建一行 Prop，每个道具可以有一张
「只拍道具、白底」的参考图，存在 Slot(owner_type="prop", kind="prop_image") 里（做法同场景图）。

镜头的 props 里有这个道具、道具有选定的图时，首帧参考图打包把道具图排在出场角色后面（domain/refs.py）。
Prop 行按说法（去首尾空白、合并空格、不分大小写）对应镜头里的道具；镜头里不再出现的道具行留着（图还在）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.locations import image_ext
from backend.domain.media import MediaStore
from backend.domain.models import Candidate, Episode, Project, Prop, Shot
from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate, selected_candidate

PROP_SLOT = "prop_image"


def prop_key(name: str) -> str:
    return " ".join(str(name or "").split()).lower()


def _clean(name) -> str:
    return " ".join(str(name).split()) if isinstance(name, str) else ""


def project_prop_uses(s: Session, project_id: int) -> dict[str, list[tuple[str, int, str]]]:
    """项目里镜头用到的道具：key → [(第一次出现时的写法, 集号, 镜头号)]，按集号、镜头顺序。"""
    rows = s.execute(select(Shot, Episode.number).join(Episode, Shot.episode_id == Episode.id)
                     .where(Episode.project_id == project_id)
                     .order_by(Episode.number, Shot.order, Shot.id)).all()
    out: dict[str, list[tuple[str, int, str]]] = {}
    for sh, number in rows:
        for p in sh.props or []:
            name = _clean(p)
            if name:
                out.setdefault(prop_key(name), []).append((name, number, sh.code))
    return out


def sync_props(s: Session, project: Project) -> list[tuple[Prop, list[tuple[str, int, str]]]]:
    """给项目里镜头用到、还没有 Prop 行的道具建行；返回镜头里出现的道具（按第一次出现的顺序）和用到它的镜头。"""
    uses = project_prop_uses(s, project.id)
    existing: dict[str, Prop] = {}
    for p in s.scalars(select(Prop).where(Prop.project_id == project.id).order_by(Prop.id)):
        existing.setdefault(prop_key(p.name), p)
    out = []
    for key, where in uses.items():
        p = existing.get(key)
        if p is None:
            p = Prop(project_id=project.id, name=where[0][0])
            s.add(p)
            s.flush()
            existing[key] = p
        out.append((p, where))
    return out


def shot_props(s: Session, shot, project_id: int) -> list[Prop]:
    """镜头 props 里写到、已经有 Prop 行的道具（按镜头里的顺序、去重）。"""
    keys = list(dict.fromkeys(prop_key(_clean(p)) for p in shot.props or [] if _clean(p)))
    if not keys:
        return []
    rows: dict[str, Prop] = {}
    for p in s.scalars(select(Prop).where(Prop.project_id == project_id).order_by(Prop.id)):
        rows.setdefault(prop_key(p.name), p)
    return [rows[k] for k in keys if k in rows]


def prop_image_of(s: Session, prop_id: int) -> Candidate | None:
    return selected_candidate(s, "prop", prop_id, PROP_SLOT)


def prop_spec(prop) -> str:
    """道具图按哪份设定出的：英文说法 + 补充描述。AI 出的道具图记在 params["spec"]，改了标过期。"""
    from backend.domain.hashing import input_hash
    return input_hash("prop_spec", (prop.name or "").strip(), (prop.visual_prompt or "").strip())


def prop_image_stale(prop, cand: Candidate | None) -> bool:
    spec = ((cand.params or {}).get("spec") if cand is not None else None)
    return bool(spec) and spec != prop_spec(prop)


def add_prop_image(s: Session, prop_id: int, *, file_path: str, engine: str, params: dict,
                   input_hash: str, job_id: int | None) -> Candidate:
    """AI 出的道具图：加成候选。已经选定了就不动（换图会让带这个道具的镜头首帧过期），没有才自动选定。"""
    slot = get_or_create_slot(s, "prop", prop_id, PROP_SLOT)
    c = add_candidate(s, slot, file_path=file_path, engine=engine, params=params,
                      input_hash=input_hash, job_id=job_id, auto_select=False)
    if slot.selected_candidate_id is None:
        select_candidate(s, c.id)
    return c


def set_prop_image(s: Session, media: MediaStore, prop: Prop, slug: str, data: bytes,
                   filename: str) -> Candidate:
    """上传的道具图：存成候选并选定。"""
    ext = image_ext(filename, "道具图")
    if not data:
        raise ValueError("道具图文件是空的")
    dst = media.new_media_path(slug, "prop", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "prop", prop.id, PROP_SLOT)
    c = add_candidate(s, slot, file_path=media.rel(dst), engine="upload",
                      params={"source": Path(filename).name},
                      input_hash=hashlib.sha256(data).hexdigest()[:16], job_id=None,
                      auto_select=False)
    return select_candidate(s, c.id)
