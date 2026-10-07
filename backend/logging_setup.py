"""后端日志：logs/studio.log，按天轮转，保留 14 天。"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_studio", False) for h in root.handlers):
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = TimedRotatingFileHandler(log_dir / "studio.log", when="midnight",
                                  backupCount=14, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    fh._studio = True  # type: ignore[attr-defined]
    root.addHandler(fh)
    root.setLevel(level)
