"""RunningHub OpenAPI 客户端（https://www.runninghub.cn）。

接口（都是 POST，JSON 里带 apiKey，返回 {code, msg, data}，code=0 成功）：
- /task/openapi/create        {workflowId, nodeInfoList}      → data.taskId   （跑云端工作流）
- /task/openapi/ai-app/run    {webappId, nodeInfoList}        → data.taskId   （跑 AI 应用）
- /task/openapi/status        {taskId}                        → data = QUEUED / RUNNING / SUCCESS / FAILED
- /task/openapi/outputs       {taskId}                        → data = [{fileUrl, fileType, nodeId}]
                                                                 失败时 data.failedReason
- /task/openapi/cancel        {taskId}
- /task/openapi/upload        multipart(apiKey, fileType, file) → data.fileName（填进 LoadImage 的 image）

nodeInfoList 是 [{nodeId, fieldName, fieldValue}]，覆盖云端工作流里对应节点的输入。
API key 从环境变量读，没有 key 时在发任何请求之前就报错。
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from backend.queue.errors import JobCanceled

STATES = ("QUEUED", "RUNNING", "SUCCESS", "FAILED")
NO_KEY_HINT = ("没有设置 RunningHub API key：在 .env 或系统环境变量里设置 {env}"
               "（RunningHub 网站 → 个人中心 → API 控制台里复制），然后重启 studio")


class RunningHubError(RuntimeError):
    """RunningHub 调用失败。信息原样给用户看。"""


class RunningHubConnError(RunningHubError):
    """连不上 RunningHub（网络抖动），轮询时可以重试。"""


POLL_RETRIES = 3     # 轮询状态时连续连不上几次就放弃（并取消云端任务）


def safe_name(url: str) -> str:
    """产物文件名：先解码再取最后一段（%2F、%5C 编码的 ../ 也只剩文件名），去掉不能用的字符。"""
    raw = unquote(urlparse(url).path).replace("\\", "/")
    name = Path(raw).name.strip().lstrip(".")
    name = "".join(ch for ch in name if ch not in '<>:"|?*' and ord(ch) >= 32)
    return name or "output.bin"


def unique_path(run_dir: Path, name: str) -> Path:
    """同名文件加 _1、_2 后缀，不覆盖。"""
    dst = run_dir / name
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 1
    while dst.exists():
        dst = run_dir / f"{stem}_{i}{suffix}"
        i += 1
    return dst


class RunningHubClient:
    def __init__(self, base_url: str, api_key: str | None, *, api_key_env: str = "RUNNINGHUB_API_KEY",
                 timeout: float = 60.0, poll_interval: float = 5.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.poll_interval = poll_interval
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False,
                                  transport=transport, follow_redirects=True)

    # ------------------------------------------------------------ 基础
    def _key(self) -> str:
        if not self.api_key:
            raise RunningHubError(NO_KEY_HINT.format(env=self.api_key_env))
        return self.api_key

    def _post(self, path: str, body: dict) -> object:
        payload = {"apiKey": self._key(), **body}
        try:
            r = self._http.post(path, json=payload)
        except httpx.TransportError as e:
            raise RunningHubConnError(f"连不上 RunningHub（{self.base_url}）：{type(e).__name__} {e}") from None
        return self._data(path, r)

    @staticmethod
    def _data(path: str, r: httpx.Response) -> object:
        if r.status_code >= 400:
            raise RunningHubError(f"RunningHub {path} 返回 HTTP {r.status_code}：{r.text[:300]}")
        try:
            d = r.json()
        except ValueError:
            raise RunningHubError(f"RunningHub {path} 返回的不是 JSON：{r.text[:300]}") from None
        if d.get("code") not in (0, None):
            raise RunningHubError(f"RunningHub {path} 报错（code {d.get('code')}）：{d.get('msg') or d}")
        return d.get("data")

    # ------------------------------------------------------------ 接口
    def create(self, *, workflow_id: str | None = None, webapp_id: str | None = None,
               node_info_list: list[dict]) -> str:
        """提交任务，返回 taskId。workflow_id 跑云端工作流，webapp_id 跑 AI 应用，二选一。"""
        if bool(workflow_id) == bool(webapp_id):
            raise RunningHubError("RunningHub 任务要给 workflow_id 或 webapp_id（只能给一个）")
        if workflow_id:
            data = self._post("/task/openapi/create",
                              {"workflowId": str(workflow_id), "nodeInfoList": node_info_list})
        else:
            data = self._post("/task/openapi/ai-app/run",
                              {"webappId": str(webapp_id), "nodeInfoList": node_info_list})
        task_id = (data or {}).get("taskId") if isinstance(data, dict) else None
        if not task_id:
            raise RunningHubError(f"RunningHub 没有返回 taskId：{data}")
        return str(task_id)

    def status(self, task_id: str) -> str:
        data = self._post("/task/openapi/status", {"taskId": task_id})
        st = data.get("taskStatus") if isinstance(data, dict) else data
        return str(st or "").upper()

    def outputs(self, task_id: str) -> object:
        """成功时是 [{fileUrl, fileType, nodeId}]；失败时可能是 {failedReason: {...}}。"""
        return self._post("/task/openapi/outputs", {"taskId": task_id})

    def cancel(self, task_id: str) -> None:
        try:
            self._post("/task/openapi/cancel", {"taskId": task_id})
        except RunningHubError:
            pass

    def upload(self, path: Path, file_type: str = "image") -> str:
        """上传参考图 / 首帧，返回云端文件名（填进工作流 LoadImage 的 image 字段）。"""
        path = Path(path)
        try:
            r = self._http.post("/task/openapi/upload",
                                data={"apiKey": self._key(), "fileType": file_type},
                                files={"file": (path.name, path.read_bytes())})
        except httpx.TransportError as e:
            raise RunningHubError(f"上传 {path.name} 到 RunningHub 失败：{e}") from None
        data = self._data("/task/openapi/upload", r)
        name = data.get("fileName") if isinstance(data, dict) else None
        if not name:
            raise RunningHubError(f"RunningHub 上传 {path.name} 没有返回文件名：{data}")
        return str(name)

    def download(self, url: str, run_dir: Path) -> Path:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        name = safe_name(url)
        try:
            r = self._http.get(url)
        except httpx.TransportError as e:
            raise RunningHubError(f"下载 RunningHub 产物失败（{name}）：{e}") from None
        if r.status_code >= 400:
            raise RunningHubError(f"下载 RunningHub 产物 {name} 返回 HTTP {r.status_code}")
        dst = unique_path(run_dir, name)
        dst.write_bytes(r.content)
        return dst

    # ------------------------------------------------------------ 等结果
    def wait(self, task_id: str, *, cancel: threading.Event, timeout: float = 1800.0,
             on_status: Callable[[str], None] | None = None) -> str:
        """轮询到 SUCCESS 返回；FAILED 抛错（带失败原因）；取消时顺手取消云端任务。"""
        deadline = time.monotonic() + timeout
        last = ""
        conn_fails = 0
        while True:
            if cancel.is_set():
                self.cancel(task_id)
                raise JobCanceled()
            try:
                st = self.status(task_id)
            except RunningHubConnError as e:
                conn_fails += 1
                if conn_fails >= POLL_RETRIES:
                    self.cancel(task_id)
                    raise RunningHubError(f"RunningHub 任务 {task_id}：连续 {conn_fails} 次{e}，"
                                          "已取消云端任务") from None
                cancel.wait(self.poll_interval)
                continue
            conn_fails = 0
            if st != last:
                last = st
                if on_status:
                    on_status(st)
            if st == "SUCCESS":
                return st
            if st == "FAILED":
                raise RunningHubError(f"RunningHub 任务 {task_id} 失败：{self._reason(task_id)}")
            if time.monotonic() >= deadline:
                self.cancel(task_id)
                raise RunningHubError(f"RunningHub 任务 {task_id} 超过 {timeout:.0f} 秒没完成，已取消")
            if cancel.wait(self.poll_interval):
                continue

    def _reason(self, task_id: str) -> str:
        try:
            data = self.outputs(task_id)
        except RunningHubError as e:
            return str(e)
        if isinstance(data, dict):
            fr = data.get("failedReason") or data
            if isinstance(fr, dict):
                return str(fr.get("exception_message") or fr.get("message") or fr)[:500]
            return str(fr)[:500]
        return "（没有给出原因）"
