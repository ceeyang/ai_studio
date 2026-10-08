"""把实体组装成 API 输出，镜头详情里带上每个槽位是否过期。"""
from __future__ import annotations

from urllib.parse import quote

from sqlalchemy.orm import Session

from backend.app.schemas import (CandidateOut, EpisodeDetail, SceneBlockOut, ShotDetail, SlotOut)
from backend.domain.slots import slots_for
from backend.pipeline.prompting import keyframe_input_hash, video_input_hash


def candidate_out(c) -> CandidateOut:
    return CandidateOut.model_validate(c).model_copy(update={"url": "/media/" + quote(c.file_path)})


def shot_detail(s: Session, sv, shot, proj, style) -> ShotDetail:
    slots = slots_for(s, "shot", shot.id)
    current: dict[str, str] = {
        "first_frame": keyframe_input_hash(shot, proj, style, shot.image_engine or proj.image_engine)}
    ff = next((x for x in slots if x.kind == "first_frame"), None)
    if ff is not None and ff.selected_candidate_id:
        ve = shot.video_engine or proj.video_engine
        try:
            has_audio = sv.engines.video(ve).caps.has_audio
        except KeyError:
            has_audio = None
        if has_audio is not None:
            current["video"] = video_input_hash(shot, proj, style, ff.selected_candidate_id, ve,
                                                has_audio)
    outs = []
    for sl in slots:
        sel = next((c for c in sl.candidates if c.id == sl.selected_candidate_id), None)
        stale = bool(sel is not None and sl.kind in current and sel.input_hash != current[sl.kind])
        outs.append(SlotOut(id=sl.id, kind=sl.kind, selected_candidate_id=sl.selected_candidate_id,
                            stale=stale, candidates=[candidate_out(c) for c in sl.candidates]))
    return ShotDetail.model_validate(shot).model_copy(update={"slots": outs})


def episode_detail(s: Session, sv, ep) -> EpisodeDetail:
    proj = ep.project
    style = proj.style_bible
    return EpisodeDetail.model_validate(ep).model_copy(update={
        "scene_blocks": [SceneBlockOut.model_validate(b) for b in ep.scene_blocks],
        "shots": [shot_detail(s, sv, sh, proj, style) for sh in ep.shots]})
