"""槽位与候选、媒体文件。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from backend.app.deps import get_or_404, services
from backend.app.schemas import CandidateOut, CandidatePatch, SlotOut
from backend.app.views import candidate_out
from backend.domain.models import Candidate
from backend.domain.slots import select_candidate, slots_for

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


@router.get("/media/{path:path}")
def media(path: str, request: Request):
    return FileResponse(services(request).media.public_file(path))
