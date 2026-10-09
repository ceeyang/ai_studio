"""把旧 ai_videos 项目（project.yaml + output/）导入为新项目。

只读原目录：所有文件都是复制进 projects/<slug>/ 下，原文件一个字节都不改。
导入的首帧/视频候选用真实的输入哈希，导入后不会显示为"已过期"。
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from backend.domain.models import Character, Episode, Export, Project, Shot
from backend.domain.slots import add_candidate, get_or_create_slot
from backend.engines.registry import ENGINE_CLASSES
from backend.pipeline.prompting import keyframe_input_hash, video_input_hash
from backend.queue.errors import JobError

SLUG_RE = re.compile(r"^[\w\-]{1,64}$")
IMAGE_ENGINES = {"minimax_h3": "h3_image", "h3": "h3_image", "zimage": "zimage"}
VIDEO_ENGINES = {"minimax_h3": "h3_video", "h3": "h3_video", "wan22": "wan22_i2v",
                 "wan22_i2v": "wan22_i2v"}
SHOT_KEYS = {"id", "refs", "image_prompt", "motion_prompt", "audio_prompt", "duration_sec",
             "transition_out", "subtitles"}
TOP_KEYS = {"title", "engines", "video", "seed", "negative", "character_refs", "shots",
            "end_card", "dub", "export_vertical"}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")
NAME_RE = re.compile(r"\(([A-Z][a-z]+(?: [A-Z][a-z]+)*)\)")
DUB_NOTE = "高棉语配音版 (Edge TTS)"


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]", "", s).lower()


def _fuzzy(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a in b or b in a)


@dataclass
class _Char:
    key: str
    ref: str | None = None
    speaker: str | None = None
    name: str = ""
    file: Path | None = None   # 复制进媒体库后的路径
    id: int | None = None      # 建好的 Character.id


@dataclass
class _Plan:
    """复制好的文件，失败时据此清理。"""
    copied: list[Path] = field(default_factory=list)


def _outputs(d: Path, prefix: str, sid: str, exts: tuple[str, ...]) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob(f"{prefix}_{sid}_*") if p.suffix.lower() in exts)


def _aspect(video: dict, warnings: list[str]) -> str:
    w, h = video.get("width"), video.get("height")
    if not w or not h:
        warnings.append("project.yaml 没写 video.width/height，画幅按 9:16")
        return "9:16"
    if w == h:
        warnings.append(f"原项目是方形 {w}x{h}，新项目只支持 9:16 / 16:9，按 9:16")
        return "9:16"
    return "9:16" if h > w else "16:9"


def _map_engine(raw, table: dict, default: str, kind: str, warnings: list[str]) -> str:
    if raw is None:
        return default
    mapped = table.get(str(raw))
    if mapped is None:
        warnings.append(f"{kind}引擎 {raw} 没有对应的新引擎，用项目默认 {default}")
        return default
    return mapped


def _characters(data: dict, shots: list[dict], warnings: list[str]) -> list[_Char]:
    speakers: list[str] = []
    for sh in shots:
        for sub in sh.get("subtitles") or []:
            sp = str(sub.get("speaker") or "").strip()
            if sp and sp not in speakers:
                speakers.append(sp)
    refs: list[str] = []
    for r in list(data.get("character_refs") or []) + [r for sh in shots for r in sh.get("refs") or []]:
        if r not in refs:
            refs.append(r)
    chars: list[_Char] = []
    for r in refs:
        chars.append(_Char(key=Path(r).stem, ref=r))
    for sp in speakers:
        hit = next((c for c in chars if c.speaker is None and _fuzzy(c.key, sp)), None)
        if hit is None:
            chars.append(_Char(key=sp, speaker=sp))
            warnings.append(f"说话人 {sp} 没有对应的定妆照")
        else:
            hit.speaker = sp
    # 可读名字：提示词里括号中的英文名（如 "Master Chen"），能对上 key 或说话人就用
    text = " ".join(str(sh.get(k) or "") for sh in shots
                    for k in ("image_prompt", "motion_prompt", "audio_prompt"))
    names = list(dict.fromkeys(NAME_RE.findall(text)))
    used: set[str] = set()
    for c in chars:
        name = next((n for n in names if n not in used and _fuzzy(n, c.key)), None)
        if name is None and c.speaker:
            name = next((n for n in names if n not in used and _fuzzy(n, c.speaker)), None)
        c.name = name or c.speaker or c.key
        if c.name in used:
            c.name = f"{c.name}_{c.key}"
        used.add(c.name)
    return chars


def _source_text(title: str, shots: list[dict], name_of) -> str:
    lines = [title, ""]
    for sh in shots:
        lines.append(f"【{sh['id']}】")
        for sub in sh.get("subtitles") or []:
            if sub.get("zh"):
                lines.append(f"{name_of(sub.get('speaker'))}：{sub['zh']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _export_variant(stem: str, folder: str, aspect: str) -> str:
    if stem == folder:
        return aspect.replace(":", "x")
    if stem.startswith(folder + "_"):
        stem = stem[len(folder) + 1:]
    return stem[:16] or aspect.replace(":", "x")


def _has_audio(services, engine: str) -> bool:
    try:
        return services.engines.video(engine).caps.has_audio
    except KeyError:
        cls = ENGINE_CLASSES.get(engine)
        return bool(cls and getattr(cls.caps, "has_audio", False))


def import_ai_videos(services, src_dir: Path, *, slug: str | None = None) -> dict:
    src = Path(src_dir).resolve()
    yml = src / "project.yaml"
    if not yml.is_file():
        raise JobError(f"{src} 下没有 project.yaml，不是 ai_videos 项目目录")
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise JobError(f"project.yaml 解析失败：{e}") from None
    slug = slug or src.name
    if not SLUG_RE.match(slug):
        raise JobError("slug 只能包含文字、数字、下划线和短横线，最长 64 个字符")
    with services.db.session() as s:
        if s.scalar(select(Project).where(Project.slug == slug)):
            raise JobError(f"项目 {slug} 已存在，换个 --slug 或先删掉旧项目")
    shots = [sh for sh in data.get("shots") or [] if isinstance(sh, dict) and sh.get("id")]
    if not shots:
        raise JobError("project.yaml 里没有镜头（shots）")

    warnings: list[str] = []
    title = str(data.get("title") or src.name)
    video = data.get("video") or {}
    engines = data.get("engines") or {}
    aspect = _aspect(video, warnings)
    image_engine = _map_engine(engines.get("image"), IMAGE_ENGINES, "zimage", "图像", warnings)
    video_engine = _map_engine(engines.get("video"), VIDEO_ENGINES, "h3_video", "视频", warnings)
    for name in (image_engine, video_engine):
        if name not in services.engines.names():
            warnings.append(f"引擎 {name} 在本机配置里没有启用，生成前需要在 config.yaml 打开")
    default_dur = float(video.get("duration_sec") or 5)
    extra_top = sorted(set(data) - TOP_KEYS)
    if extra_top:
        warnings.append(f"project.yaml 里这些字段没有导入（原样记在导入记录里）：{', '.join(extra_top)}")
    for sh in shots:
        extra = sorted(set(sh) - SHOT_KEYS)
        if extra:
            warnings.append(f"镜头 {sh['id']} 的字段没有导入（原样记在导入记录里）：{', '.join(extra)}")

    chars = _characters(data, shots, warnings)
    by_ref = {c.ref: c for c in chars if c.ref}
    by_speaker = {c.speaker: c for c in chars if c.speaker}

    def name_of(speaker) -> str:
        c = by_speaker.get(str(speaker or ""))
        return c.name if c else str(speaker or "")

    media = services.media
    plan = _Plan()

    def copy(path: Path, kind: str) -> Path:
        dst = media.new_media_path(slug, kind, path.suffix)
        shutil.copy2(path, dst)
        plan.copied.append(dst)
        return dst

    out = src / "output"
    try:
        # 第一步：复制文件（慢，不占数据库写锁）
        for c in chars:
            if c.ref:
                f = src / c.ref
                if f.is_file():
                    c.file = copy(f, "portrait")
                else:
                    warnings.append(f"定妆照 {c.ref} 不存在")
        shot_files: dict[str, dict[str, list[tuple[Path, Path]]]] = {}
        for sh in shots:
            sid = str(sh["id"])
            got = {"first_frame": [(p, copy(p, "first_frame"))
                                   for p in _outputs(out / "keyframes", "kf", sid, IMAGE_EXTS)],
                   "video": [(p, copy(p, "video"))
                             for p in _outputs(out / "clips", "clip", sid, VIDEO_EXTS)],
                   "dub": [(p, copy(p, "video"))
                           for p in _outputs(out / "clips_dub", "clip", sid, VIDEO_EXTS)]}
            if not got["first_frame"]:
                warnings.append(f"镜头 {sid} 没有首帧（output/keyframes/kf_{sid}_*）")
            if not got["video"]:
                warnings.append(f"镜头 {sid} 没有视频（output/clips/clip_{sid}_*）")
            shot_files[sid] = got
        finals = sorted((out / "final").glob("*.mp4")) if (out / "final").is_dir() else []
        export_files: list[tuple[Path, Path]] = []
        for f in finals:
            dst = media.export_path(slug, f.name)
            shutil.copy2(f, dst)
            plan.copied.append(dst)
            export_files.append((f, dst))
        rec_dir = media.project_dir(slug) / "imported"
        rec_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(yml, rec_dir / "project.yaml")
        plan.copied.append(rec_dir / "project.yaml")

        not_imported = []
        for sub, pat in (("dub", "*.mp3"), ("final", "*.ass")):
            n = len(list((out / sub).glob(pat))) if (out / sub).is_dir() else 0
            if n:
                not_imported.append(f"output/{sub}/{pat}（{n} 个）")
        if not_imported:
            warnings.append("没有对应模型、未导入：" + "，".join(not_imported)
                            + "；原文件仍在源目录")

        # 第二步：写数据库（一个短事务）
        counts = {"characters": 0, "portraits": 0, "shots": 0, "keyframes": 0, "videos": 0,
                  "dub_videos": 0, "exports": 0}
        with services.db.session() as s:
            if s.scalar(select(Project).where(Project.slug == slug)):
                raise JobError(f"项目 {slug} 已存在")
            proj = Project(slug=slug, title=title[:200], aspect=aspect, image_engine=image_engine,
                           video_engine=video_engine,
                           seed=int(data.get("seed") if data.get("seed") is not None else 12345),
                           negative=" ".join(str(data.get("negative") or "").split()))
            s.add(proj)
            s.flush()
            voices = ((data.get("dub") or {}).get("voices") or {})
            for c in chars:
                ch = Character(project_id=proj.id, name=c.name[:100],
                               description=f"从 ai_videos 导入（原 key：{c.speaker or c.key}）",
                               voice=voices.get(c.speaker) if c.speaker else None)
                s.add(ch)
                s.flush()
                c.id = ch.id
                counts["characters"] += 1
                if c.file:
                    slot = get_or_create_slot(s, "character", ch.id, "portrait")
                    cand = add_candidate(s, slot, file_path=media.rel(c.file), engine="imported",
                                         params={"imported_from": str(src / c.ref)}, input_hash="",
                                         job_id=None)
                    slot.selected_candidate_id = cand.id
                    counts["portraits"] += 1

            total = sum(float(sh.get("duration_sec") or default_dur) for sh in shots)
            ep = Episode(project_id=proj.id, number=1, title=title[:200],
                         source_text=_source_text(title, shots, name_of),
                         target_seconds=round(total, 2),
                         status="exported" if export_files else "storyboarded")
            s.add(ep)
            s.flush()
            style = proj.style_bible
            for i, sh in enumerate(shots):
                sid = str(sh["id"])
                dialogue = []
                for sub in sh.get("subtitles") or []:
                    t = sub.get("t") or [0, 0]
                    line = {"speaker": name_of(sub.get("speaker")), "zh": sub.get("zh") or "",
                            "start": float(t[0]), "end": float(t[1])}
                    if sub.get("km"):
                        line["km"] = sub["km"]
                    dialogue.append(line)
                cast, seen = [], set()
                for r in sh.get("refs") or []:
                    c = by_ref.get(r)
                    if c and c.id not in seen:
                        seen.add(c.id)
                        cast.append({"character_id": c.id, "variation_id": None})
                shot = Shot(episode_id=ep.id, order=i, code=f"S{i + 1:02d}",
                            image_prompt=" ".join(str(sh.get("image_prompt") or "").split()),
                            motion_prompt=" ".join(str(sh.get("motion_prompt") or "").split()),
                            audio_prompt=" ".join(str(sh.get("audio_prompt") or "").split()),
                            duration=float(sh.get("duration_sec") or default_dur),
                            transition_out=str(sh.get("transition_out") or "hard_cut")[:32],
                            dialogue=dialogue, cast=cast)
                s.add(shot)
                s.flush()
                counts["shots"] += 1
                if shot.code != sid:
                    warnings.append(f"镜头 {sid} 按顺序编号为 {shot.code}")
                files = shot_files[sid]
                ff_id = None
                if files["first_frame"]:
                    h = keyframe_input_hash(shot, proj, style, shot.image_engine or image_engine)
                    slot = get_or_create_slot(s, "shot", shot.id, "first_frame")
                    for orig, dst in files["first_frame"]:
                        cand = add_candidate(s, slot, file_path=media.rel(dst), engine=image_engine,
                                             params={"imported_from": str(orig)}, input_hash=h,
                                             job_id=None, auto_select=False)
                        counts["keyframes"] += 1
                    slot.selected_candidate_id = ff_id = cand.id  # 多张时选最新那张
                if files["video"] or files["dub"]:
                    ve = shot.video_engine or video_engine
                    h = (video_input_hash(shot, proj, style, ff_id, ve, _has_audio(services, ve))
                         if ff_id else "")
                    slot = get_or_create_slot(s, "shot", shot.id, "video")
                    main = None
                    for orig, dst in files["video"]:
                        main = add_candidate(s, slot, file_path=media.rel(dst), engine=ve,
                                             params={"imported_from": str(orig),
                                                     "first_frame_candidate_id": ff_id},
                                             input_hash=h, job_id=None, auto_select=False)
                        counts["videos"] += 1
                    for orig, dst in files["dub"]:
                        d = add_candidate(s, slot, file_path=media.rel(dst), engine=ve,
                                          params={"imported_from": str(orig),
                                                  "first_frame_candidate_id": ff_id,
                                                  "dub": "edge_tts", "dub_text": "km"},
                                          input_hash=h, job_id=None, auto_select=False)
                        d.note = DUB_NOTE
                        counts["dub_videos"] += 1
                    if main is not None:
                        slot.selected_candidate_id = main.id
            for orig, dst in export_files:
                s.add(Export(episode_id=ep.id, variant=_export_variant(orig.stem, src.name, aspect),
                             file_path=media.rel(dst),
                             settings={"imported_from": str(orig), "clips": len(shots),
                                       "label": orig.stem}))
                counts["exports"] += 1
            s.flush()
            episode_id = ep.id

        record = {"source": str(src), "imported_at": datetime.now().isoformat(timespec="seconds"),
                  "engines": engines, "video": video, "end_card": data.get("end_card"),
                  "dub": data.get("dub"), "export_vertical": data.get("export_vertical"),
                  "character_refs": data.get("character_refs"),
                  "characters": [{"key": c.key, "speaker": c.speaker, "ref": c.ref, "name": c.name}
                                 for c in chars],
                  "extra_fields": {k: data[k] for k in extra_top},
                  "extra_shot_fields": {str(sh["id"]): {k: sh[k] for k in sorted(set(sh) - SHOT_KEYS)}
                                        for sh in shots if set(sh) - SHOT_KEYS},
                  "warnings": warnings}
        (rec_dir / "ai_videos.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except BaseException:
        for p in plan.copied:
            p.unlink(missing_ok=True)
        raise
    return {"slug": slug, "url": f"/projects/{slug}", "project_title": title,
            "episode_id": episode_id, "counts": counts, "warnings": warnings}
