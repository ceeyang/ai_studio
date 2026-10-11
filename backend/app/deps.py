"""路由公用：取服务、按 id/slug 查找、引擎校验、异常映射。"""
from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.models import Project
from backend.queue.errors import JobError

LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$")


def is_local_origin(origin: str) -> bool:
    """浏览器页面来自本机（127.0.0.1 / localhost，任意端口）。HTTP 写请求和 WebSocket 共用。"""
    return bool(LOCAL_ORIGIN.match(origin))


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


async def read_upload(request: Request, field: str = "file") -> tuple[bytes, str]:
    """multipart 上传的文件：返回 (内容, 原文件名)。不是 multipart 或缺字段时报中文错误。"""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise JobError(f"要用 multipart/form-data 上传，文件放在 {field} 字段")
    form = await request.form()
    up = form.get(field)
    if up is None or isinstance(up, str):
        raise JobError(f"缺少上传文件字段 {field}")
    return await up.read(), up.filename or ""
