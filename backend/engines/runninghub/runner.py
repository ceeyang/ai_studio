"""执行 RunningHub 引擎调用：上传首帧/参考图 → 提交任务 → 轮询状态 → 下载产物到任务目录。"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path

from backend.engines.base import UPLOAD_PREFIX, EngineCall
from backend.engines.runninghub.client import RunningHubClient, RunningHubError

STATE_TEXT = {"QUEUED": "排队中", "RUNNING": "运行中", "SUCCESS": "完成", "FAILED": "失败"}


class RunningHubRunner:
    def __init__(self, client: RunningHubClient):
        self.client = client

    def prepare(self, request: dict) -> dict:
        """把 @upload: 标记的本地图换成上传后的云端文件名（同一张图只传一次）。"""
        req = json.loads(json.dumps(request))
        done: dict[str, str] = {}
        for item in req.get("node_info_list") or []:
            v = item.get("fieldValue")
            if isinstance(v, str) and v.startswith(UPLOAD_PREFIX):
                local = v[len(UPLOAD_PREFIX):]
                if local not in done:
                    done[local] = self.client.upload(Path(local))
                item["fieldValue"] = done[local]
        return req

    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            log: Callable[[str], None] | None = None) -> list[Path]:
        emit = log or (lambda m: None)
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        req = self.prepare(call.workflow)
        (run_dir / "workflow.api.json").write_text(json.dumps(req, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
        (run_dir / "call.json").write_text(json.dumps(
            {"engine": call.engine, "output_kind": call.output_kind, "runner": "runninghub",
             "meta": call.meta, "dropped_refs": call.dropped_refs},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        task_id = self.client.create(workflow_id=req.get("workflow_id"), webapp_id=req.get("webapp_id"),
                                     node_info_list=req.get("node_info_list") or [])
        emit(f"RunningHub 任务 {task_id} 已提交")
        self.client.wait(task_id, cancel=cancel, timeout=float(req.get("timeout") or 1800.0),
                         on_status=lambda st: emit(f"RunningHub 任务 {task_id}：{STATE_TEXT.get(st, st)}"))
        outs = self.client.outputs(task_id)
        urls = [o["fileUrl"] for o in (outs if isinstance(outs, list) else [])
                if isinstance(o, dict) and o.get("fileUrl")]
        if not urls:
            raise RunningHubError(f"RunningHub 任务 {task_id} 完成了，但没有产出文件（检查云端工作流的保存节点）")
        return [self.client.download(u, run_dir) for u in urls]
