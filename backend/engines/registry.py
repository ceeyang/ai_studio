"""引擎注册表：按 config.yaml 的 engines.<name>.enabled 加载。"""
from __future__ import annotations

from typing import Any

from backend.config import Config
from backend.engines.base import ImageCaps, VideoCaps
from backend.engines.comfyui.engines import (H3ImageEngine, H3VideoEngine, Wan22I2VEngine,
                                             ZImageEngine)
from backend.engines.comfyui.template import WorkflowTemplate

ENGINE_CLASSES = {cls.name: cls for cls in (ZImageEngine, H3ImageEngine, H3VideoEngine,
                                            Wan22I2VEngine)}


class EngineRegistry:
    def __init__(self, cfg: Config):
        self._engines: dict[str, Any] = {}
        for name, cls in ENGINE_CLASSES.items():
            ec = cfg.engine(name)
            if not ec.enabled:
                continue
            tpl = WorkflowTemplate.load(cfg.workflows_dir, cls.template_name)
            self._engines[name] = cls(tpl, ec.params)

    def get(self, name: str) -> Any:
        try:
            return self._engines[name]
        except KeyError:
            raise KeyError(f"引擎 {name} 不存在或未启用，可用：{', '.join(self.names())}") from None

    def image(self, name: str) -> Any:
        e = self.get(name)
        if not isinstance(e.caps, ImageCaps):
            raise KeyError(f"{name} 不是图像引擎")
        return e

    def video(self, name: str) -> Any:
        e = self.get(name)
        if not isinstance(e.caps, VideoCaps):
            raise KeyError(f"{name} 不是视频引擎")
        return e

    def names(self, kind: str | None = None) -> list[str]:
        want = {"image": ImageCaps, "video": VideoCaps}.get(kind or "")
        return sorted(n for n, e in self._engines.items() if want is None or isinstance(e.caps, want))

    def all(self) -> dict[str, Any]:
        return dict(self._engines)
