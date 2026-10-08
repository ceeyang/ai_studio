"""槽位与候选、媒体文件、导出到 ComfyUI。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from backend.app.deps import get_or_404, services
from backend.app.schemas import CandidateOut, CandidatePatch, SlotOut
from backend.app.views import candidate_out
from backend.domain.models import Candidate
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


@router.get("/media/{path:path}")
def media(path: str, request: Request):
    return FileResponse(services(request).media.public_file(path))
