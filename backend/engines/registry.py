"""引擎注册表：按 config.yaml 的 engines.<name>.enabled 加载。

ComfyUI 引擎带工作流模板（template_name），网页引擎（gpt_image / gemini_image）没有模板；
RunningHub 云端引擎只在 config.yaml 的 runninghub.engines 里配了才有。"""
from __future__ import annotations

from typing import Any

from backend.config import Config
from backend.engines.base import ImageCaps, VideoCaps
from backend.engines.browser.engines import BROWSER_ENGINES
from backend.engines.comfyui.engines import (H3ImageEngine, H3VideoEngine, Krea2Engine,
                                             Wan22I2VEngine, ZImageEngine)
from backend.engines.comfyui.template import WorkflowTemplate
from backend.engines.runninghub.engines import load_runninghub_engines
from backend.queue.errors import JobError

ENGINE_CLASSES = {cls.name: cls for cls in (ZImageEngine, Krea2Engine, H3ImageEngine, H3VideoEngine,
                                            Wan22I2VEngine, *BROWSER_ENGINES)}


def runtime_of(engine: Any) -> str:
    """comfyui（本机显卡）、browser（驱动已登录的网页）或 runninghub（云端）。"""
    return getattr(engine, "runtime", "comfyui")


def check_ready(engine: Any) -> None:
    """提交任务前的检查（比如 RunningHub 没有 API key）：不行就抛 JobError，信息给用户看。"""
    pre = getattr(engine, "preflight", None)
    msg = pre() if callable(pre) else None
    if msg:
        raise JobError(msg)


class EngineRegistry:
    def __init__(self, cfg: Config):
        self._engines: dict[str, Any] = {}
        self.load_errors: dict[str, str] = {}   # 配错没加载的引擎 → 原因（doctor 报）
        for name, cls in ENGINE_CLASSES.items():
            ec = cfg.engine(name)
            if not ec.enabled:
                continue
            if getattr(cls, "template_name", None):
                tpl = WorkflowTemplate.load(cfg.workflows_dir, cls.template_name)
                self._engines[name] = cls(tpl, ec.params)
            else:
                self._engines[name] = cls(ec.params)
        rh, self.load_errors = load_runninghub_engines(cfg, set(ENGINE_CLASSES))
        self._engines.update(rh)

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
