"""路由公用：取服务、按 id/slug 查找、引擎校验、异常映射。"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import Project
from backend.queue.errors import JobError


def services(request: Request):
    return request.app.state.services


def manager(request: Request):
    return request.app.state.manager


def get_or_404(s: Session, model, obj_id: int, label: str):
    obj = s.get(model, obj_id)
    if obj is None:
        raise LookupError(f"找不到{label} {obj_id}")
    return obj


def project_by_slug(s: Session, slug: str) -> Project:
    p = s.scalar(select(Project).where(Project.slug == slug))
    if p is None:
        raise LookupError(f"找不到项目 {slug}")
    return p


def check_engines(sv, image_engine: str | None, video_engine: str | None) -> None:
    if image_engine is not None and image_engine not in sv.engines.names("image"):
        raise JobError(f"图像引擎 {image_engine} 不存在或未启用，可选：{', '.join(sv.engines.names('image'))}")
    if video_engine is not None and video_engine not in sv.engines.names("video"):
        raise JobError(f"视频引擎 {video_engine} 不存在或未启用，可选：{', '.join(sv.engines.names('video'))}")


def install_error_handlers(app: FastAPI) -> None:
    def handler(status: int):
        async def _h(request, exc):
            return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=status)
        return _h

    app.add_exception_handler(JobError, handler(400))
    app.add_exception_handler(ValueError, handler(400))
    app.add_exception_handler(LookupError, handler(404))
    app.add_exception_handler(PermissionError, handler(403))
