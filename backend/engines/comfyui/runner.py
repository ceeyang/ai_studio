"""执行 EngineCall：上传参考图 → 提交 → 等结果（WebSocket 推进度）→ 下载产物。

产物通过 /view 下载到 run_dir，不依赖 ComfyUI 的 output 目录在哪里。
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from backend.engines.base import UPLOAD_PREFIX, EngineCall
from backend.engines.comfyui.client import ComfyClient
from backend.engines.comfyui.errors import ComfyError, execution_error
from backend.queue.errors import JobCanceled

OUTPUT_KEYS = ("images", "gifs", "videos", "audio")


class ComfyRunner:
    def __init__(self, client: ComfyClient, *, poll_interval: float = 1.0, use_ws: bool = True):
        self.client = client
        self.poll_interval = poll_interval
        self.use_ws = use_ws

    def prepare(self, workflow: dict) -> dict:
        wf = json.loads(json.dumps(workflow))
        for node in wf.values():
            inputs = node.get("inputs") or {}
            for key, val in list(inputs.items()):
                if isinstance(val, str) and val.startswith(UPLOAD_PREFIX):
                    inputs[key] = self.client.upload_image(Path(val[len(UPLOAD_PREFIX):]))
        return wf

    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            timeout: float = 3600.0) -> list[Path]:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        wf = self.prepare(call.workflow)
        (run_dir / "workflow.api.json").write_text(
            json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "call.json").write_text(json.dumps(
            {"engine": call.engine, "output_kind": call.output_kind,
             "model_family": call.model_family, "meta": call.meta,
             "dropped_refs": call.dropped_refs},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        ws = self._open_ws()
        try:
            pid = self.client.queue_prompt(wf)
            entry = self._wait(pid, ws, cancel, on_progress, timeout)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        err = execution_error(entry)
        if err is not None:
            raise err
        return self._download(entry, run_dir)

    def cancel_current(self) -> None:
        for fn in (self.client.interrupt, self.client.clear_queue, self.client.free):
            try:
                fn()
            except ComfyError:
                pass

    def free(self) -> None:
        try:
            self.client.free()
        except ComfyError:
            pass

    # ------------------------------------------------------------
    def _open_ws(self):
        if not self.use_ws:
            return None
        try:
            return ws_connect(self.client.ws_url, open_timeout=5, max_size=None, proxy=None,
                              legacy=True)
        except TypeError:  # 旧版 websockets 没有 proxy/legacy 参数
            try:
                return ws_connect(self.client.ws_url, open_timeout=5, max_size=None)
            except Exception:
                return None
        except Exception:
            return None

    def _wait(self, pid: str, ws, cancel: threading.Event,
              on_progress: Callable[[int, int], None] | None, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        next_check = 0.0
        while True:
            if cancel.is_set():
                self.cancel_current()
                raise JobCanceled()
            if time.monotonic() > deadline:
                self.cancel_current()
                raise ComfyError(f"ComfyUI 任务 {pid} 超过 {timeout:.0f} 秒没完成，已中断",
                                 retryable=True)
            if ws is not None:
                try:
                    msg = ws.recv(timeout=self.poll_interval)
                    if isinstance(msg, str):
                        self._handle(msg, pid, on_progress)
                except TimeoutError:
                    pass
                except ConnectionClosed:
                    ws = None
            else:
                time.sleep(self.poll_interval)
            now = time.monotonic()
            if now >= next_check:
                next_check = now + self.poll_interval
                entry = self.client.history(pid)
                if entry is not None:
                    return entry

    @staticmethod
    def _handle(raw: str, pid: str, on_progress: Callable[[int, int], None] | None) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if msg.get("type") != "progress" or on_progress is None:
            return
        data = msg.get("data") or {}
        if data.get("prompt_id") in (pid, None):
            on_progress(int(data.get("value", 0)), int(data.get("max", 0)))

    def _download(self, entry: dict, run_dir: Path) -> list[Path]:
        paths: list[Path] = []
        for out in (entry.get("outputs") or {}).values():
            for key in OUTPUT_KEYS:
                for f in out.get(key) or []:
                    if not isinstance(f, dict) or "filename" not in f or f.get("type") == "temp":
                        continue
                    dst = run_dir / f["filename"]
                    dst.write_bytes(self.client.view(f["filename"], f.get("subfolder", ""),
                                                     f.get("type", "output")))
                    paths.append(dst)
        if not paths:
            raise ComfyError("ComfyUI 任务完成了，但没有产出文件（检查工作流里的保存节点）")
        return paths
