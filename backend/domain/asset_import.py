"""续季 / 跨项目复用资产：把一个项目里的角色、场景、道具（连同定妆照、三视图、场景图、道具图）复制到另一个项目。

新一季通常是一个新项目。资产按行复制（外貌提示词、声音、变体、三视图描述……都带上），槽位里默认只复制
选定的那张候选（candidates="all" 时全部复制），媒体文件拷进目标项目自己的 media 目录（新路径），选定关系保持。
复制完两个项目互不影响：改目标项目的角色、删源项目，都不会动到另一边。

重名：默认跳过并写进报告（on_conflict="skip"）；"rename" 时在名字后面加后缀（默认「（续）」，可配置，
还重名就再加序号）。道具按英文说法（不分大小写、合并空格）判重名。

with_style=True：沿用源项目的风格设定——把源项目的 StyleBible 内容（和项目负面提示词）复制成目标项目自己的一份
（不共用同一行：put_style 是原地改的，共用会让改一个项目的风格连带改了另一个）。

候选之间的引用（三视图格子的 sheet_candidate_id、设定图的 portrait_candidate_id 等 params 里的 *_candidate_id）
换成复制后的新 id；没复制过来的引用清空，不留指向别的项目的 id。三视图设定图的输入哈希里有定妆照的 id，
在源项目里是最新的就按新定妆照 id 重算，免得一导入就显示过期。
"""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.media import MediaStore
from backend.domain.models import (Candidate, Character, Location, Project, Prop, Slot, StyleBible,
                                   Variation)
from backend.domain.props import prop_key
from backend.domain.slots import slots_for

AssetKind = Literal["character", "location", "prop"]
KINDS: tuple[AssetKind, ...] = ("character", "location", "prop")
KIND_LABELS = {"character": "角色", "location": "场景", "prop": "道具"}
DEFAULT_SUFFIX = "（续）"

_CHAR_FIELDS = ("description", "visual_prompt", "core_features", "voice", "design_options",
                "turnaround_look", "voice_only")
_LOC_FIELDS = ("time_of_day", "atmosphere", "visual_prompt")
_PROP_FIELDS = ("description", "visual_prompt")
_STYLE_FIELDS = ("palette", "lighting", "texture", "character_rules", "mood_keywords",
                 "anchor_text", "negative")
_MODEL = {"character": Character, "location": Location, "prop": Prop}


@dataclass
class ImportedItem:
    kind: str
    source_id: int
    id: int
    name: str
    renamed_from: str | None = None
    files: int = 0


@dataclass
class SkippedItem:
    kind: str
    source_id: int
    name: str
    reason: str


@dataclass
class ImportReport:
    imported: list[ImportedItem] = field(default_factory=list)
    skipped: list[SkippedItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files_copied: int = 0
    style_copied: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _key(kind: str, name: str) -> str:
    return prop_key(name) if kind == "prop" else (name or "").strip()


def _unique_name(name: str, suffix: str, taken: set[str], kind: str) -> str:
    base = f"{name}{suffix}"
    cand, n = base, 2
    while _key(kind, cand) in taken:
        # 「林夏（续）」也有了：「林夏（续2）」；后缀以括号收尾时序号放进括号里
        cand = (f"{base[:-1]}{n}{base[-1]}" if suffix and suffix[-1] in "）)]】"
                else f"{base}{n}")
        n += 1
    return cand


def _pick_rows(s: Session, kind: str, source: Project, ids: list[int] | None,
               take_all: bool) -> list:
    model = _MODEL[kind]
    rows = list(s.scalars(select(model).where(model.project_id == source.id).order_by(model.id)))
    if take_all:
        return rows
    if not ids:
        return []
    by_id = {r.id: r for r in rows}
    missing = [i for i in dict.fromkeys(ids) if i not in by_id]
    if missing:
        raise LookupError(f"源项目 {source.slug} 里没有这些{KIND_LABELS[kind]}："
                          f"{', '.join(map(str, missing))}")
    return [by_id[i] for i in dict.fromkeys(ids)]


class _Copier:
    """复制槽位和媒体文件；出错时删掉已经拷出来的文件（数据库由会话回滚）。"""

    def __init__(self, s: Session, media: MediaStore, target: Project, candidates: str,
                 report: ImportReport):
        self.s, self.media, self.target = s, media, target
        self.candidates, self.report = candidates, report
        self.written: list[Path] = []

    def _copy_file(self, rel: str, owner_type: str) -> str | None:
        try:
            src = self.media.abs(rel)
        except ValueError:
            return None
        if not src.is_file():
            return None
        parts = Path(rel).parts
        sub = parts[2] if len(parts) >= 4 and parts[1] == "media" else owner_type
        dst = self.media.new_media_path(self.target.slug, sub, src.suffix or ".bin")
        shutil.copy2(src, dst)
        self.written.append(dst)
        return self.media.rel(dst)

    def copy_slots(self, owner_type: str, src_id: int, dst_id: int, label: str
                   ) -> tuple[dict[int, Candidate], int]:
        """返回 (源候选 id → 新候选, 拷贝的文件数)。"""
        id_map: dict[int, Candidate] = {}
        files = 0
        for slot in slots_for(self.s, owner_type, src_id):
            if self.candidates == "all":
                todo = list(slot.candidates)
            else:
                todo = [c for c in slot.candidates if c.id == slot.selected_candidate_id]
            if not todo:
                continue
            new_slot = Slot(owner_type=owner_type, owner_id=dst_id, kind=slot.kind)
            self.s.add(new_slot)
            self.s.flush()
            for c in todo:
                path = self._copy_file(c.file_path, owner_type)
                if path is None:
                    self.report.warnings.append(f"{label}：{slot.kind} 的文件 {c.file_path} 找不到，没有复制")
                    continue
                nc = Candidate(slot_id=new_slot.id, file_path=path, engine=c.engine,
                               params=dict(c.params or {}), input_hash=c.input_hash, job_id=None,
                               note=c.note, rating=c.rating, created_at=c.created_at)
                self.s.add(nc)
                self.s.flush()
                id_map[c.id] = nc
                files += 1
                if c.id == slot.selected_candidate_id:
                    new_slot.selected_candidate_id = nc.id
            if not any(x.slot_id == new_slot.id for x in id_map.values()):
                self.s.delete(new_slot)
        self.s.flush()
        # 候选之间的引用换成新 id；没复制过来的清空（不留指向源项目的 id）
        for nc in id_map.values():
            params = dict(nc.params or {})
            changed = False
            for k, v in list(params.items()):
                if k.endswith("_candidate_id") and isinstance(v, int):
                    params[k] = id_map[v].id if v in id_map else None
                    changed = True
            if changed:
                nc.params = params
        self.report.files_copied += files
        return id_map, files

    def cleanup(self) -> None:
        for p in self.written:
            p.unlink(missing_ok=True)


def _refresh_sheet_hashes(s: Session, src: Character,
                          id_map: dict[int, Candidate]) -> None:
    """三视图设定图的输入哈希里有选定定妆照的 id（引擎吃参考图时）：源项目里是最新的，
    按复制后的定妆照 id 重算，格子的哈希跟着换。源项目里本来就过期的保持原样（目标里也是过期）。"""
    from backend.domain.characters import portrait_of
    from backend.domain.turnaround import (SHEET_KEYS, SHEETS, cell_hash, legacy_sheet_hash,
                                           sheet_hash, sheet_inputs, look_of, sheet_slot_kind,
                                           view_slot_kind)

    old_p = portrait_of(s, src.id)
    if old_p is None or old_p.id not in id_map:
        return
    new_pid = id_map[old_p.id].id
    style = src.project.style_bible
    anchor = style.anchor_text if style else ""
    look = look_of(src)
    kinds = {sheet_slot_kind(k): k for k in SHEET_KEYS}
    renamed: dict[str, str] = {}
    for old_id, nc in id_map.items():
        slot = s.get(Slot, nc.slot_id)
        sheet = kinds.get(slot.kind) if slot else None
        if sheet is None:
            continue
        _, negative = sheet_inputs(src, sheet)
        for fn in (sheet_hash, legacy_sheet_hash):
            if nc.input_hash == fn(sheet, look, anchor, negative, nc.engine, old_p.id):
                new = fn(sheet, look, anchor, negative, nc.engine, new_pid)
                renamed[nc.input_hash] = new
                nc.input_hash = new
                break
    if not renamed:
        return
    views = {view_slot_kind(v): v for spec in SHEETS.values() for v in spec.views}
    for nc in id_map.values():
        slot = s.get(Slot, nc.slot_id)
        view = views.get(slot.kind) if slot else None
        if view is None:
            continue
        for old, new in renamed.items():
            if nc.input_hash == cell_hash(old, view):
                nc.input_hash = cell_hash(new, view)
                break
    s.flush()


def copy_style(s: Session, source: Project, target: Project) -> bool:
    """把源项目的风格设定复制成目标项目自己的一份，连同项目的负面提示词（两样都进出图输入，
    不一致的话复制过来的三视图会显示过期）。源项目没有风格设定时只带负面词，返回 False。"""
    target.negative = source.negative
    sb = source.style_bible
    if sb is None:
        s.flush()
        return False
    mine = target.style_bible
    if mine is None or mine.id == sb.id:
        mine = StyleBible()
        s.add(mine)
        target.style_bible = mine
    for k in _STYLE_FIELDS:
        v = getattr(sb, k)
        setattr(mine, k, list(v) if isinstance(v, list) else v)
    s.flush()
    return True


def import_assets(s: Session, media: MediaStore, *, source: Project, target: Project,
                  character_ids: list[int] | None = None, location_ids: list[int] | None = None,
                  prop_ids: list[int] | None = None, all_assets: bool = False,
                  kinds: tuple[str, ...] | list[str] | None = None,
                  on_conflict: str = "skip", rename_suffix: str = DEFAULT_SUFFIX,
                  candidates: str = "selected", with_style: bool = False) -> ImportReport:
    """把 source 里选中的角色 / 场景 / 道具复制到 target。

    all_assets=True：kinds（默认三类都要）里的全部复制，忽略 *_ids。
    on_conflict："skip"（重名的跳过，写进报告）/ "rename"（名字加 rename_suffix）。
    candidates："selected"（每个槽位只复制选定的那张）/ "all"。"""
    if source.id == target.id:
        raise ValueError("不能从项目自己导入资产")
    if on_conflict not in ("skip", "rename"):
        raise ValueError(f"重名处理只能是 skip / rename，收到：{on_conflict}")
    if candidates not in ("selected", "all"):
        raise ValueError(f"候选只能是 selected / all，收到：{candidates}")
    suffix = (rename_suffix or "").strip() or DEFAULT_SUFFIX
    want = set(kinds or KINDS)
    bad = want - set(KINDS)
    if bad:
        raise ValueError(f"资产种类只能是 {' / '.join(KINDS)}，收到：{', '.join(sorted(bad))}")
    ids = {"character": character_ids, "location": location_ids, "prop": prop_ids}

    report = ImportReport()
    copier = _Copier(s, media, target, candidates, report)
    try:
        if with_style:
            report.style_copied = copy_style(s, source, target)
        for kind in KINDS:
            if all_assets and kind not in want:
                continue
            rows = _pick_rows(s, kind, source, ids[kind], all_assets)
            model = _MODEL[kind]
            taken = {_key(kind, n) for n in s.scalars(
                select(model.name).where(model.project_id == target.id))}
            for row in rows:
                name, renamed_from = row.name, None
                if _key(kind, name) in taken:
                    if on_conflict == "skip":
                        report.skipped.append(SkippedItem(
                            kind, row.id, name, f"目标项目里已经有同名{KIND_LABELS[kind]}"))
                        continue
                    renamed_from, name = name, _unique_name(name, suffix, taken, kind)
                new = _copy_row(s, kind, row, target, name)
                taken.add(_key(kind, name))
                id_map, files = copier.copy_slots(kind, row.id, new.id,
                                                  f"{KIND_LABELS[kind]}「{row.name}」")
                if kind == "character":
                    _refresh_sheet_hashes(s, row, id_map)
                report.imported.append(ImportedItem(kind, row.id, new.id, name, renamed_from,
                                                    files))
        s.flush()
    except BaseException:
        copier.cleanup()
        raise
    return report


def _copy_row(s: Session, kind: str, row, target: Project, name: str):
    if kind == "character":
        new = Character(project_id=target.id, name=name,
                        **{k: _clone(getattr(row, k)) for k in _CHAR_FIELDS})
        s.add(new)
        s.flush()
        for v in row.variations:
            s.add(Variation(character_id=new.id, name=v.name, visual_prompt=v.visual_prompt))
    elif kind == "location":
        new = Location(project_id=target.id, name=name,
                       **{k: getattr(row, k) for k in _LOC_FIELDS})
        s.add(new)
    else:
        new = Prop(project_id=target.id, name=name, **{k: getattr(row, k) for k in _PROP_FIELDS})
        s.add(new)
    s.flush()
    return new


def _clone(v):
    if isinstance(v, list):
        return [dict(x) if isinstance(x, dict) else x for x in v]
    if isinstance(v, dict):
        return dict(v)
    return v
