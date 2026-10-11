"""槽位与候选、媒体文件、导出到 ComfyUI。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select as sa_select

from backend.app.deps import get_or_404, services
from backend.app.schemas import (ArchivedCandidateOut, CandidateOut, CandidatePatch, RestoreArchivedIn,
                                 SlotOut)
from backend.app.views import archived_out, candidate_out
from backend.domain.archive import restore_archived
from backend.domain.models import ArchivedCandidate, Candidate, Episode
from backend.domain.slots import select_candidate, slots_for
from backend.engines.comfyui.errors import ComfyError
from backend.engines.comfyui.export_ui import info_lookup, to_ui_graph

router = APIRouter(tags=["candidates"])


@router.get("/api/slots", response_model=list[SlotOut])
def list_slots(owner_type: str, owner_id: int, request: Request):
    with services(request).db.session() as s:
        return [SlotOut(id=sl.id, kind=sl.kind, selected_candidate_id=sl.selected_candidate_id,
                        candidates=[candidate_out(c) for c in sl.candidates])
                for sl in slots_for(s, owner_type, owner_id)]


@router.post("/api/candidates/{candidate_id}/select", response_model=CandidateOut)
def select(candidate_id: int, request: Request):
    with services(request).db.session() as s:
        return candidate_out(select_candidate(s, candidate_id))


@router.patch("/api/candidates/{candidate_id}", response_model=CandidateOut)
def patch_candidate(candidate_id: int, body: CandidatePatch, request: Request):
    with services(request).db.session() as s:
        c = get_or_404(s, Candidate, candidate_id, "候选")
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(c, k, v)
        s.flush()
        return candidate_out(c)


@router.get("/api/candidates/{candidate_id}/file")
def candidate_file(candidate_id: int, request: Request):
    sv = services(request)
    with sv.db.session() as s:
        rel = get_or_404(s, Candidate, candidate_id, "候选").file_path
    return FileResponse(sv.media.public_file(rel))


@router.get("/api/candidates/{candidate_id}/comfy-workflow")
def comfy_workflow(candidate_id: int, request: Request):
    """把这个候选当时用的工作流转成 ComfyUI 界面格式，作为附件下载，拖进 ComfyUI 就能手调。"""
    sv = services(request)
    with sv.db.session() as s:
        c = get_or_404(s, Candidate, candidate_id, "候选")
        rel, engine = (c.params or {}).get("workflow"), c.engine or "workflow"
    if not rel:
        raise LookupError(f"候选 {candidate_id} 没有记录工作流")
    path = sv.media.abs(rel)  # runs/ 不对外公开，只在这里内部读取
    if not path.is_file():
        raise LookupError(f"工作流文件不存在：{rel}")
    api_wf = json.loads(path.read_text(encoding="utf-8"))
    if sv.comfy_client is None:
        raise HTTPException(503, "没有配置 ComfyUI，取不到节点信息")
    try:
        info = sv.comfy_client.object_info(tries=1, timeout=30)
    except ComfyError as e:
        raise HTTPException(502, f"取不到 ComfyUI 的节点信息：{e}") from None
    graph = to_ui_graph(api_wf, info_lookup(info))
    name = f"{engine}_{candidate_id}.json"
    return JSONResponse(graph, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------ 历史候选
@router.get("/api/episodes/{episode_id}/archived-candidates",
            response_model=list[ArchivedCandidateOut])
def list_archived(episode_id: int, request: Request, kind: str | None = None):
    """重跑分镜删掉的镜头留下的候选，新归档的在前。kind 只看首帧（first_frame）或视频（video）。"""
    with services(request).db.session() as s:
        get_or_404(s, Episode, episode_id, "剧集")
        q = sa_select(ArchivedCandidate).where(ArchivedCandidate.episode_id == episode_id)
        if kind:
            q = q.where(ArchivedCandidate.kind == kind)
        rows = s.scalars(q.order_by(ArchivedCandidate.archived_at.desc(),
                                    ArchivedCandidate.id.desc()))
        return [archived_out(a) for a in rows]


@router.post("/api/archived-candidates/{archived_id}/restore", response_model=CandidateOut)
def restore(archived_id: int, body: RestoreArchivedIn, request: Request):
    """恢复到这个镜头同类槽位上（槽位还没选定时就选它），并从历史候选里去掉。"""
    with services(request).db.session() as s:
        return candidate_out(restore_archived(s, archived_id, body.shot_id))


@router.delete("/api/archived-candidates/{archived_id}", status_code=204)
def purge_archived(archived_id: int, request: Request):
    """从历史候选里删掉这条记录（媒体文件留在磁盘上）。"""
    with services(request).db.session() as s:
        s.delete(get_or_404(s, ArchivedCandidate, archived_id, "历史候选"))
    return Response(status_code=204)


@router.get("/media/{path:path}")
def media(path: str, request: Request):
    return FileResponse(services(request).media.public_file(path))
