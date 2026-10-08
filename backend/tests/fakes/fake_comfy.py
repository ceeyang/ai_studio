"""测试用的假 ComfyUI：真实 HTTP + WebSocket 服务，跑在后台线程。"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import uuid

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import JSONResponse, Response


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeComfy:
    def __init__(self):
        self.prompts: dict[str, dict] = {}
        self.uploads: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.reject: dict | None = None        # /prompt 返回 400 时的 body
        self.exec_error: dict | None = None    # history 里放 execution_error
        self.hold = False                      # True：任务一直不完成，直到 finish_all()
        self.progress_steps = 3
        self.output_name = "out_00001_.png"
        self.output_kind = "images"
        self.output_bytes = b"fake-output"
        self.object_info: dict = {}
        self.events: list[str] = []
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.app = self._build()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def finish_all(self) -> None:
        for p in self.prompts.values():
            p["done"] = True

    def last_workflow(self) -> dict:
        return list(self.prompts.values())[-1]["workflow"]

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.get("/system_stats")
        def system_stats():
            self.calls.append("system_stats")
            return {"system": {"ram_free": 32 * 2**30},
                    "devices": [{"name": "fake", "vram_free": 12 * 2**30,
                                 "vram_total": 16 * 2**30}]}

        @app.get("/object_info")
        def object_info():
            return self.object_info

        @app.post("/prompt")
        async def prompt(req: Request):
            body = await req.json()
            self.calls.append("prompt")
            if self.reject is not None:
                return JSONResponse(self.reject, status_code=400)
            pid = uuid.uuid4().hex
            self.prompts[pid] = {"workflow": body["prompt"], "done": not self.hold}
            for i in range(1, self.progress_steps + 1):
                self.events.append(json.dumps({"type": "progress", "data": {
                    "value": i, "max": self.progress_steps, "prompt_id": pid, "node": "8"}}))
            return {"prompt_id": pid, "number": len(self.prompts), "node_errors": {}}

        @app.get("/history/{pid}")
        def history(pid: str):
            p = self.prompts.get(pid)
            if not p or not p["done"]:
                return {}
            if self.exec_error is not None:
                return {pid: {"outputs": {}, "status": {
                    "status_str": "error", "completed": False,
                    "messages": [["execution_start", {}], ["execution_error", self.exec_error]]}}}
            out = {self.output_kind: [{"filename": self.output_name, "subfolder": "studio",
                                       "type": "output"}]}
            return {pid: {"outputs": {"9": out}, "status": {
                "status_str": "success", "completed": True, "messages": []}}}

        @app.get("/view")
        def view(filename: str, subfolder: str = "", type: str = "output"):
            self.calls.append(f"view:{subfolder}/{filename}")
            return Response(self.output_bytes, media_type="application/octet-stream")

        @app.post("/upload/image")
        async def upload(image: UploadFile = File(...), overwrite: str = Form("false"),
                         type: str = Form("input")):
            self.uploads[image.filename] = await image.read()
            self.calls.append(f"upload:{image.filename}")
            return {"name": image.filename, "subfolder": "", "type": "input"}

        @app.post("/interrupt")
        def interrupt():
            self.calls.append("interrupt")
            return {}

        @app.post("/queue")
        def queue():
            self.calls.append("clear_queue")
            return {}

        @app.post("/free")
        def free():
            self.calls.append("free")
            return {}

        @app.websocket("/ws")
        async def ws(sock: WebSocket):
            sent = len(self.events)  # 只推连上之后产生的事件；必须在 accept() 之前读，
            # 否则客户端握手一结束就能发 POST /prompt 追加事件，而这里还没读到旧长度，
            # 会把刚追加的新事件当成"连接前就有的"而漏推
            await sock.accept()
            try:
                while True:
                    while sent < len(self.events):
                        await sock.send_text(self.events[sent])
                        sent += 1
                    await asyncio.sleep(0.02)
            except Exception:
                pass

        return app

    def start(self) -> None:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                timeout_graceful_shutdown=1)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        end = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > end:
                raise RuntimeError("FakeComfy 启动超时")
            time.sleep(0.02)

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(5)
