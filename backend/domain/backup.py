"""数据库自动备份：会删东西的重跑（分镜重出删镜头）动手前，用 SQLite 的在线备份 API 拷一份。

备份放在数据库旁边的 backups/（数据库就在 data_dir 下，所以是 <data_dir>/backups/），
文件名 studio-<YYYYmmdd-HHMMSS>-<原因>.db，只留最新的 BACKUP_KEEP 份。备份失败只记警告、不拦任务。"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_KEEP = 10


def backup_dir(db) -> Path:
    return Path(db.path).parent / "backups"


def _slug(reason: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", reason).strip("-")[:40] or "auto"


def backup_database(db, reason: str, *, keep: int = BACKUP_KEEP, now: str | None = None) -> Path | None:
    """拷一份数据库，返回备份文件；失败记警告返回 None。"""
    try:
        root = backup_dir(db)
        root.mkdir(parents=True, exist_ok=True)
        stamp = now or datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = root / f"studio-{stamp}-{_slug(reason)}.db"
        n = 1
        while dst.exists():   # 同一秒里又备份一次
            n += 1
            dst = root / f"studio-{stamp}-{_slug(reason)}-{n}.db"
        src = sqlite3.connect(Path(db.path))
        try:
            out = sqlite3.connect(dst)
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
        prune_backups(root, keep)
        return dst
    except Exception as e:  # noqa: BLE001  备份失败不能让任务失败
        log.warning("自动备份数据库失败：%s", e)
        return None


def prune_backups(root: Path, keep: int = BACKUP_KEEP) -> None:
    """只留最新的 keep 份（文件名以时间开头，按名字排就是按时间排）。"""
    files = sorted(root.glob("studio-*.db"), key=lambda p: (p.name[7:22], p.stat().st_mtime))
    for old in files[:-keep] if keep > 0 else files:
        try:
            old.unlink()
        except OSError as e:
            log.warning("删除旧备份 %s 失败：%s", old.name, e)
