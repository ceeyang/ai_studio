"""从旧工具导入项目。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.deps import services
from backend.importers.ai_videos import import_ai_videos
from backend.queue.errors import JobError

router = APIRouter(prefix="/api/import", tags=["import"])


class AiVideosImportIn(BaseModel):
    path: str = Field(min_length=1)
    slug: str | None = None


@router.post("/ai-videos")
def import_ai_videos_route(body: AiVideosImportIn, request: Request) -> dict:
    """同步 def：FastAPI 放进线程池跑，复制大文件时不卡事件循环。"""
    src = Path(body.path).expanduser()
    if not src.is_absolute():
        raise JobError("path 必须是绝对路径")
    if not src.is_dir():
        raise JobError(f"目录不存在：{src}")
    return import_ai_videos(services(request), src, slug=body.slug or None)
