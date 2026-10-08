"""ComfyUI HTTP 客户端。移植自 ai_videos/vglib/core.py，改用 httpx。

和旧版的区别：
- 只有连接失败、超时、5xx 才重试；4xx（工作流校验失败、模型文件不存在）立即失败并带出错节点。
- 参考图走 /upload/image，按内容哈希命名，同一张图只传一次，不再往 input 目录直接写文件。
- trust_env=False：绕开系统代理，否则 localhost 请求会挂住。
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

import httpx

from backend.engines.comfyui.errors import ComfyError, error_from_response


class ComfyClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0, max_retries: int = 6,
                 backoff: float = 1.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.max_retries = max(1, max_retries)
        self.backoff = backoff
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False,
                                  transport=transport)
        self._uploaded: dict[str, str] = {}

    @property
    def ws_url(self) -> str:
        url = self.base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{url}/ws?clientId={self.client_id}"

    def _request(self, method: str, path: str, *, tries: int | None = None,
                 timeout: float | None = None, **kw) -> httpx.Response:
        tries = tries or self.max_retries
        if timeout is not None:
            kw["timeout"] = timeout
        last = ""
        for attempt in range(tries):
            try:
                r = self._http.request(method, path, **kw)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code < 400:
                    return r
                if r.status_code < 500:
                    raise error_from_response(path, r)
                last = f"HTTP {r.status_code}: {r.text[:300]}"
            if attempt + 1 < tries:
                time.sleep(self.backoff * min(attempt + 1, 5))
        raise ComfyError(f"连不上 ComfyUI（{self.base_url}{path}），重试 {tries} 次仍失败：{last}",
                         retryable=True)

    def system_stats(self, *, tries: int | None = None, timeout: float | None = None) -> dict:
        return self._request("GET", "/system_stats", tries=tries, timeout=timeout).json()

    def object_info(self, *, tries: int | None = None, timeout: float = 120) -> dict:
        return self._request("GET", "/object_info", tries=tries, timeout=timeout).json()

    def queue_prompt(self, workflow: dict) -> str:
        r = self._request("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        return r.json()["prompt_id"]

    def history(self, prompt_id: str) -> dict | None:
        return self._request("GET", f"/history/{prompt_id}").json().get(prompt_id)

    def upload_image(self, path: Path) -> str:
        path = Path(path)
        data = path.read_bytes()
        digest = hashlib.sha1(data).hexdigest()[:16]
        if digest in self._uploaded:
            return self._uploaded[digest]
        name = f"studio_{digest}{path.suffix.lower() or '.png'}"
        r = self._request("POST", "/upload/image",
                          files={"image": (name, data, "application/octet-stream")},
                          data={"overwrite": "true", "type": "input"}, timeout=180)
        d = r.json()
        ref = f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]
        self._uploaded[digest] = ref
        return ref

    def view(self, filename: str, subfolder: str = "", type: str = "output") -> bytes:
        return self._request("GET", "/view", params={"filename": filename, "subfolder": subfolder,
                                                     "type": type}, timeout=300).content

    def interrupt(self) -> None:
        self._request("POST", "/interrupt", json={}, tries=3)

    def clear_queue(self) -> None:
        self._request("POST", "/queue", json={"clear": True}, tries=3)

    def free(self) -> None:
        self._request("POST", "/free", json={"unload_models": True, "free_memory": True}, tries=3)

    def vram_free_gb(self) -> float | None:
        try:
            return self.system_stats(tries=2)["devices"][0]["vram_free"] / 2**30
        except (ComfyError, KeyError, IndexError):
            return None
