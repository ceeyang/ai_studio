"""媒体文件路径。数据库里只存相对 data_dir 的 posix 路径。"""
from __future__ import annotations

import uuid
from pathlib import Path

PUBLIC_DIRS = {"media", "exports"}


class MediaStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, slug: str) -> Path:
        return self.root / slug

    def new_media_path(self, slug: str, kind: str, ext: str) -> Path:
        d = self.root / slug / "media" / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{uuid.uuid4().hex[:12]}.{ext.lstrip('.').lower()}"

    def run_dir(self, slug: str, job_id: int) -> Path:
        d = self.root / slug / "runs" / str(job_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def export_path(self, slug: str, name: str) -> Path:
        d = self.root / slug / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"路径越界：{rel}")
        return p

    def public_file(self, rel: str) -> Path:
        p = self.abs(rel)
        parts = p.relative_to(self.root).parts
        if len(parts) < 3 or parts[1] not in PUBLIC_DIRS:
            raise PermissionError(f"不允许访问：{rel}")
        return p
