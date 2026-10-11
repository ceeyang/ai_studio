"""资产选择器：列出本项目 / 本集已经有的图片（角色定妆照和三视图、场景图、道具图、镜头的首帧和视频、
历史候选、已上传的构图参考），镜头抽屉里可以直接拿来当构图参考或首帧。

资产 id 是字符串：候选 "cand-<id>"，历史候选 "arch-<id>"。视频资产要说取哪一帧（first / last），
用的时候用 ffmpeg 截出来。拿来用的图一律复制一份到镜头自己的媒体目录（原候选被删、历史候选被清掉
也不影响），候选 engine 记成 "asset"，params 里写明来自哪里。
"""
from __future__ import annotations

import hashlib
import io
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import (ArchivedCandidate, Candidate, Character, Episode, Location, Prop,
                                   Shot, Slot)

ASSET_ENGINE = "asset"
CATEGORIES = ("character", "location", "prop", "shot", "archived", "shot_ref")
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FRAMES = {"first": "首帧", "last": "尾帧"}

_SHOT_KIND_LABEL = {"first_frame": "首帧", "end_frame": "尾帧", "video": "视频"}
_CHAR_KIND_ORDER = ("portrait", "turnaround_sheet_body", "turnaround_sheet_face")


@dataclass
class Asset:
    id: str
    kind: str              # 分类：character / location / prop / shot / archived / shot_ref
    slot_kind: str         # 槽位：portrait / location_image / first_frame / video …
    media: str             # image / video
    label: str             # 「陈师傅 定妆照」「S03 首帧」
    source: str            # 角色名 / 场景名 / 道具说法 / 镜头号
    file_path: str
    width: int | None = None
    height: int | None = None
    selected: bool = False
    shot_id: int | None = None
    owner_id: int | None = None
    engine: str = ""
    created_at: datetime | None = None


def media_kind(file_path: str) -> str | None:
    ext = Path(file_path or "").suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return None


def _public(file_path: str) -> bool:
    parts = (file_path or "").split("/")
    return len(parts) >= 3 and parts[1] in ("media", "exports")


@lru_cache(maxsize=4096)
def _size(path: str, mtime: float) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:  # noqa: BLE001 — 读不出尺寸就不给
        return None, None


def image_size(media, rel: str) -> tuple[int | None, int | None]:
    try:
        p = media.abs(rel)
        return _size(str(p), p.stat().st_mtime)
    except (OSError, ValueError):
        return None, None


def char_kind_label(kind: str) -> str | None:
    from backend.domain.turnaround import SHEETS, VIEW_LABELS

    if kind == "portrait":
        return "定妆照"
    if kind == "appearance_ref":
        return "外观参考照片"
    if kind.startswith("turnaround_sheet_"):
        spec = SHEETS.get(kind.removeprefix("turnaround_sheet_"))
        return f"三视图·{spec.label}" if spec else None
    if kind.startswith("turnaround_view_"):
        v = VIEW_LABELS.get(kind.removeprefix("turnaround_view_"))
        return f"三视图·{v}" if v else None
    return None


def _char_order(kind: str) -> tuple[int, str]:
    if kind in _CHAR_KIND_ORDER:
        return _CHAR_KIND_ORDER.index(kind), kind
    return (len(_CHAR_KIND_ORDER) + (0 if kind.startswith("turnaround_view_") else 1), kind)


def _slots(s: Session, owner_type: str, ids: list[int]) -> list[Slot]:
    if not ids:
        return []
    return list(s.scalars(select(Slot).where(Slot.owner_type == owner_type, Slot.owner_id.in_(ids))
                          .order_by(Slot.id)))


def _cands(slot: Slot) -> list[Candidate]:
    """选定的排最前，其余新的在前。"""
    return sorted(slot.candidates, key=lambda c: (c.id != slot.selected_candidate_id, -c.id))


def list_assets(s: Session, media, ep: Episode, kind: str | None = None) -> list[Asset]:
    if kind and kind not in CATEGORIES:
        raise ValueError(f"资产分类只能是 {' / '.join(CATEGORIES)}，收到：{kind}")
    want = (kind,) if kind else CATEGORIES
    proj = ep.project
    out: list[Asset] = []

    def add(c, cat: str, slot_kind: str, label: str, source: str, *, selected=False,
            shot_id=None, owner_id=None):
        m = media_kind(c.file_path)
        if m is None or not _public(c.file_path):
            return
        w, h = image_size(media, c.file_path) if m == "image" else (None, None)
        out.append(Asset(id=f"{'arch' if isinstance(c, ArchivedCandidate) else 'cand'}-{c.id}",
                         kind=cat, slot_kind=slot_kind, media=m, label=label, source=source,
                         file_path=c.file_path, width=w, height=h, selected=selected,
                         shot_id=shot_id, owner_id=owner_id, engine=c.engine or "",
                         created_at=c.created_at))

    if "character" in want:
        chars = list(proj.characters)
        slots = _slots(s, "character", [c.id for c in chars])
        for ch in chars:
            mine = sorted((x for x in slots if x.owner_id == ch.id), key=lambda x: _char_order(x.kind))
            for sl in mine:
                what = char_kind_label(sl.kind)
                if what is None:
                    continue
                for c in _cands(sl):
                    add(c, "character", sl.kind, f"{ch.name} {what}", ch.name,
                        selected=c.id == sl.selected_candidate_id, owner_id=ch.id)
    if "location" in want:
        from backend.domain.locations import LOCATION_SLOT
        locs = list(proj.locations)
        slots = _slots(s, "location", [x.id for x in locs])
        for loc in locs:
            for sl in (x for x in slots if x.owner_id == loc.id and x.kind == LOCATION_SLOT):
                for c in _cands(sl):
                    add(c, "location", sl.kind, f"场景图·{loc.name}", loc.name,
                        selected=c.id == sl.selected_candidate_id, owner_id=loc.id)
    if "prop" in want:
        from backend.domain.props import PROP_SLOT
        from backend.domain.refs import prop_label
        props = list(s.scalars(select(Prop).where(Prop.project_id == proj.id).order_by(Prop.id)))
        slots = _slots(s, "prop", [p.id for p in props])
        for p in props:
            for sl in (x for x in slots if x.owner_id == p.id and x.kind == PROP_SLOT):
                for c in _cands(sl):
                    add(c, "prop", sl.kind, f"道具·{prop_label(p.name)}", p.name,
                        selected=c.id == sl.selected_candidate_id, owner_id=p.id)
    shots = list(ep.shots)
    if "shot" in want or "shot_ref" in want:
        from backend.domain.refs import SHOT_REF_SLOT
        slots = _slots(s, "shot", [sh.id for sh in shots])
        for sh in shots:
            mine = [x for x in slots if x.owner_id == sh.id]
            if "shot" in want:
                for k in ("first_frame", "end_frame", "video"):
                    for sl in (x for x in mine if x.kind == k):
                        for c in _cands(sl):
                            add(c, "shot", k, f"{sh.code} {_SHOT_KIND_LABEL[k]}", sh.code,
                                selected=c.id == sl.selected_candidate_id, shot_id=sh.id,
                                owner_id=sh.id)
            if "shot_ref" in want:
                for sl in (x for x in mine if x.kind == SHOT_REF_SLOT):
                    for i, c in enumerate(sl.candidates, start=1):
                        add(c, "shot_ref", sl.kind, f"{sh.code} 构图参考 {i}", sh.code,
                            shot_id=sh.id, owner_id=sh.id)
    if "archived" in want:
        rows = s.scalars(select(ArchivedCandidate).where(ArchivedCandidate.episode_id == ep.id)
                         .order_by(ArchivedCandidate.archived_at.desc(), ArchivedCandidate.id.desc()))
        for a in rows:
            what = {**_SHOT_KIND_LABEL, "shot_ref": "构图参考"}.get(a.kind)
            if what is None:
                continue
            code = a.shot_code or "已删镜头"
            add(a, "archived", a.kind, f"{code} {what}（历史）", code, selected=a.was_selected)
    return out


# ------------------------------------------------------------ 用资产
def _candidate_project(s: Session, c: Candidate) -> int | None:
    slot = s.get(Slot, c.slot_id)
    if slot is None:
        return None
    owner = {"shot": Shot, "character": Character, "location": Location, "prop": Prop}.get(
        slot.owner_type)
    if owner is None:
        return None
    obj = s.get(owner, slot.owner_id)
    if obj is None:
        return None
    if isinstance(obj, Shot):
        ep = s.get(Episode, obj.episode_id)
        return ep.project_id if ep is not None else None
    return obj.project_id


def _describe(s: Session, c: Candidate) -> str:
    """候选来自哪里（「陈师傅 定妆照」「S03 视频」「场景图·修车行」）。"""
    slot = s.get(Slot, c.slot_id)
    if slot.owner_type == "character":
        ch = s.get(Character, slot.owner_id)
        return f"{ch.name} {char_kind_label(slot.kind) or slot.kind}"
    if slot.owner_type == "location":
        return f"场景图·{s.get(Location, slot.owner_id).name}"
    if slot.owner_type == "prop":
        from backend.domain.refs import prop_label
        return f"道具·{prop_label(s.get(Prop, slot.owner_id).name)}"
    sh = s.get(Shot, slot.owner_id)
    from backend.domain.refs import SHOT_REF_SLOT
    what = "构图参考" if slot.kind == SHOT_REF_SLOT else _SHOT_KIND_LABEL.get(slot.kind, slot.kind)
    return f"{sh.code} {what}"


@dataclass
class PickedAsset:
    asset_id: str
    file_path: str
    label: str
    media: str


def resolve_asset(s: Session, project_id: int, asset_id: str) -> PickedAsset:
    """资产 id → 文件和来源说明；只能用本项目的资产。"""
    prefix, _, num = str(asset_id or "").partition("-")
    if prefix not in ("cand", "arch") or not num.isdigit():
        raise ValueError(f"资产 id 不对：{asset_id}")
    if prefix == "cand":
        c = s.get(Candidate, int(num))
        if c is None or _candidate_project(s, c) != project_id:
            raise LookupError(f"本项目没有这个资产：{asset_id}")
        label = _describe(s, c)
    else:
        c = s.get(ArchivedCandidate, int(num))
        if c is None or c.project_id != project_id:
            raise LookupError(f"本项目没有这个资产：{asset_id}")
        what = {**_SHOT_KIND_LABEL, "shot_ref": "构图参考"}.get(c.kind, c.kind)
        label = f"{c.shot_code or '已删镜头'} {what}（历史）"
    m = media_kind(c.file_path)
    if m is None:
        raise ValueError(f"{label} 不是图片或视频，不能拿来用")
    return PickedAsset(asset_id, c.file_path, label, m)


def asset_image(sv, picked: PickedAsset, frame: str | None) -> tuple[bytes, str, str]:
    """资产的图片内容：(字节, 扩展名, 说明)。视频按 frame 截首帧 / 尾帧。"""
    src = sv.media.abs(picked.file_path)
    if not src.is_file():
        raise LookupError(f"{picked.label} 的文件不见了（{picked.file_path}）")
    if picked.media == "image":
        return src.read_bytes(), src.suffix.lower(), picked.label
    if frame not in FRAMES:
        raise ValueError(f"{picked.label} 是视频：要说明取首帧还是尾帧")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.png"
        if frame == "last":
            sv.ffmpeg.last_frame(src, out)
        else:
            sv.ffmpeg.first_frame(src, out)
        data = out.read_bytes()
    return data, ".png", f"{picked.label}·{FRAMES[frame]}"


def parse_aspect(aspect: str) -> tuple[int, int] | None:
    try:
        w, h = (int(x) for x in str(aspect).split(":"))
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def center_crop(data: bytes, aspect: str) -> tuple[bytes, bool]:
    """居中裁切成 aspect（如 9:16）。返回 (PNG 字节, 是否真的裁了)；已经是这个画幅就原样返回。"""
    from PIL import Image

    ratio = parse_aspect(aspect)
    if ratio is None:
        return data, False
    with Image.open(io.BytesIO(data)) as im:
        w, h = im.size
        want = ratio[0] / ratio[1]
        if abs(w / h - want) < 0.01:
            return data, False
        if w / h > want:
            nw = max(1, round(h * want))
            box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
        else:
            nh = max(1, round(w / want))
            box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
        buf = io.BytesIO()
        im.crop(box).save(buf, "PNG")
    return buf.getvalue(), True


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def add_shot_refs_from_assets(s: Session, sv, shot, slug: str, project_id: int,
                              picks: list[tuple[str, str | None]]) -> list[Candidate]:
    """把选中的资产复制成这一镜的构图参考图（和上传的一样受每镜 MAX_SHOT_REFS 张的上限）。"""
    from backend.domain.refs import MAX_SHOT_REFS, SHOT_REF_SLOT, shot_ref_images
    from backend.domain.slots import add_candidate, get_or_create_slot

    if not picks:
        raise ValueError("没有选资产")
    have = len(shot_ref_images(s, shot.id))
    if have + len(picks) > MAX_SHOT_REFS:
        left = MAX_SHOT_REFS - have
        raise ValueError(f"每个镜头最多 {MAX_SHOT_REFS} 张构图参考图，"
                         + (f"现在只能再加 {left} 张" if left > 0 else "先删掉一张再加"))
    out = []
    for asset_id, frame in picks:
        picked = resolve_asset(s, project_id, asset_id)
        data, ext, label = asset_image(sv, picked, frame)
        dst = sv.media.new_media_path(slug, "shot_ref", ext)
        dst.write_bytes(data)
        slot = get_or_create_slot(s, "shot", shot.id, SHOT_REF_SLOT)
        out.append(add_candidate(s, slot, file_path=sv.media.rel(dst), engine=ASSET_ENGINE,
                                 params={"source": "asset", "asset": asset_id, "from": label,
                                         **({"frame": frame} if picked.media == "video" else {})},
                                 input_hash=_digest(data), job_id=None, auto_select=False))
    return out


def add_first_frame_from_asset(s: Session, sv, shot, slug: str, project_id: int, aspect: str,
                               asset_id: str, frame: str | None = None, *, crop: bool = False,
                               select: bool = True) -> Candidate:
    """拿一张已有的图当这一镜的首帧候选（复制一份；crop 时居中裁成项目画幅），select 时设为选定。"""
    from backend.domain.slots import add_candidate, get_or_create_slot, select_candidate

    picked = resolve_asset(s, project_id, asset_id)
    data, ext, label = asset_image(sv, picked, frame)
    cropped = False
    if crop:
        data, cropped = center_crop(data, aspect)
        if cropped:
            ext = ".png"
    dst = sv.media.new_media_path(slug, "first_frame", ext)
    dst.write_bytes(data)
    slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
    c = add_candidate(s, slot, file_path=sv.media.rel(dst), engine=ASSET_ENGINE,
                      params={"source": "asset", "asset": asset_id, "from": label,
                              **({"frame": frame} if picked.media == "video" else {}),
                              "cropped": cropped,
                              "inputs": {"engine": ASSET_ENGINE, "from": label, "refs": []}},
                      input_hash=f"asset:{_digest(data)}", job_id=None, auto_select=False)
    if select or slot.selected_candidate_id is None:
        select_candidate(s, c.id)
    return c
