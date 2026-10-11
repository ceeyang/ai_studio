"""扩展桥：出图在用户平时用的浏览器里跑（transport=extension）。

browser-extension/ 是一个 MV3 扩展，装在用户日常用的 Chrome/Edge 里。它的 service worker 用 WebSocket
连 /api/browser/ext（只接受 chrome-extension:// 来源，见 app/routes/browser_ext.py），studio 把出图任务
发给它，它在那个浏览器里已登录的 ChatGPT / Gemini 标签页里开新对话、传参考图、输提示词、等图、在页面里
带 cookie 取原图，再把图片分块传回来。studio 不启动浏览器、不读也不拷用户的 cookie。

协议（JSON 文本帧）：
  扩展 → studio
    hello    {version, sites: [...], features: [...], browser, ext_id}   连上后第一条（features 里有 rpc 才支持网页文本模型，v0.2 起）
    ping     {t}                                              每 ~20 秒，studio 回 pong（也让 MV3 的 SW 不被挂起）
    status   {sites: {key: {tabs, logged_in, cookie, page}}}  各网站开着几个标签页、像不像已登录
    progress {job_id, value, max}
    log      {job_id, message}
    image    {job_id, index, seq, total, mime, src, via, data}   结果图 base64 分块（单帧别超过 uvicorn 的 16MB）
    result   {job_id, count}                                  全部分块发完
    error    {job_id, kind, message}                          中文原因，kind 同 BrowserError
    opened   {urls}                                           open 的回执
    rpc_result {id, ok, result | kind+error}                  rpc 的回执
  studio → 扩展
    welcome  {server_version, options}
    job      {job_id, site, prompt, refs: [{name, mime, b64}], aspect, timeout, options, conversation_url}
             conversation_url（v0.3 起）：在这条之前的对话里接着出图（打不开就新开）；result 里带回
             conversation {url, continued, note}：结束时标签页地址、是否真的续接上了、退回新对话的原因
    cancel   {job_id}
    status_request {}
    open     {urls}                                           在用户浏览器里打开这些网址（登录用）
    rpc      {id, method, params}                             网页文本模型一步步驱动页面（v0.2 起）：
             acquire_tab {site} → {tab_id, fresh, how}        专门写文本的标签页（不用出图的那个）
             goto {tab_id, url} → url / url {tab_id} → url
             dom {tab_id, op, args} → content/dom.js 的 op 的返回值（只许 RPC_DOM_OPS 里那些）
             cookie_names {url} → [名字]

一次只认一个扩展连接：新连接来了，旧的关掉（同一个浏览器里扩展重载、或换了个浏览器）。
等结果的是队列的网页通道线程（同步），WebSocket 在事件循环里（异步）：中间用每个任务一个 queue.Queue。
"""
from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
import queue
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from backend.engines.browser.errors import EXT_INSTALL_HINT, BrowserError, excerpt
from backend.queue.errors import JobCanceled

PROTOCOL = 1
RPC_FEATURE = "rpc"          # 扩展 hello 里报的能力：有它才能用网页文本模型（v0.2 起）
CONVERSATION_FEATURE = "conversation"   # 有它出图任务才能续接之前的网页会话、报回对话地址（v0.3 起）
RPC_TIMEOUT = 90.0
KINDS = {"not_logged_in", "quota", "refused", "timeout", "no_image", "page", "browser"}
# 扩展那边开标签页、等页面加载、传参考图都不算在 timeout（等出图）里，多给这么久再判超时
JOB_GRACE_SECONDS = 120.0
SEND_TIMEOUT = 60.0
_DISCONNECTED = {"type": "_disconnected"}


@dataclass
class _Conn:
    sock: Any
    loop: asyncio.AbstractEventLoop
    origin: str
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    hello: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, msg: dict) -> None:
        async with self.lock:
            await self.sock.send_json(msg)


@dataclass
class ExtImage:
    data: bytes
    mime: str
    src: str
    via: str


def ref_payload(path: Path) -> dict:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return {"name": p.name, "mime": mime, "b64": base64.b64encode(p.read_bytes()).decode()}


def extension_id_of(origin: str) -> str:
    return origin.split("://", 1)[1].strip("/") if "://" in origin else ""


class ExtensionHub:
    """持有当前扩展连接；网页通道线程通过 generate() 发任务、等结果。"""

    def __init__(self, options: dict | None = None):
        self.options = dict(options or {})
        self._lock = threading.Lock()
        self._conn: _Conn | None = None
        self._jobs: dict[str, queue.Queue] = {}
        self._sites: dict[str, dict] = {}
        self._status_at: float | None = None
        self.last_disconnect: float | None = None

    # ------------------------------------------------------------ 状态
    @property
    def connected(self) -> bool:
        return self._conn is not None

    def info(self) -> dict:
        c = self._conn
        if c is None:
            return {"connected": False, "last_disconnect": self.last_disconnect}
        h = c.hello
        return {"connected": True, "version": h.get("version"), "browser": h.get("browser"),
                "ext_id": extension_id_of(c.origin), "sites": h.get("sites") or [],
                "features": h.get("features") or [],
                "connected_at": c.connected_at, "last_seen": c.last_seen,
                "status_at": self._status_at}

    def site_status(self) -> dict[str, dict]:
        """扩展最近一次报的各网站状态：{key: {tabs, logged_in, cookie, page}}；没连上时为空。"""
        return dict(self._sites) if self._conn is not None else {}

    # ------------------------------------------------------------ 事件循环这边（WebSocket 路由调用）
    async def serve(self, sock, origin: str) -> None:
        """路由已经查过来源、accept 过了；这里收消息直到断开。"""
        conn = _Conn(sock, asyncio.get_running_loop(), origin)
        with self._lock:
            old, self._conn = self._conn, conn
            self._sites = {}
            self._status_at = None
        if old is not None:   # 新的扩展连接替换旧的：旧连接上还在等的任务马上失败，别干等超时
            self._fail_pending("扩展换了一个新连接（扩展被重新加载或另一个浏览器连上来了）")
            try:
                await old.sock.close(code=4000)
            except Exception:
                pass
        await conn.send({"type": "welcome", "protocol": PROTOCOL, "options": self.options})
        try:
            while True:
                msg = await sock.receive_json()
                conn.last_seen = time.time()
                await self._on_message(conn, msg)
        except Exception:
            pass   # 断开（WebSocketDisconnect）或收到不是 JSON 的东西：都按断开处理
        finally:
            with self._lock:
                current = self._conn is conn
                if current:
                    self._conn = None
                    self._sites = {}
                    self.last_disconnect = time.time()
            if current:
                self._fail_pending("扩展连接断开了（浏览器关了、扩展被停用或重新加载）")

    async def _on_message(self, conn: _Conn, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        t = msg.get("type")
        if t == "hello":
            conn.hello = msg
        elif t == "ping":
            await conn.send({"type": "pong", "t": msg.get("t")})
        elif t == "status":
            sites = msg.get("sites")
            if isinstance(sites, dict):
                self._sites = {str(k): dict(v) for k, v in sites.items() if isinstance(v, dict)}
                self._status_at = time.time()
        elif t == "rpc_result":
            q = self._jobs.get(str(msg.get("id")))
            if q is not None:
                q.put(msg)
        elif t in ("progress", "log", "image", "result", "error"):
            q = self._jobs.get(str(msg.get("job_id")))
            if q is not None:
                q.put(msg)

    def _fail_pending(self, why: str) -> None:
        for q in list(self._jobs.values()):
            q.put({**_DISCONNECTED, "why": why})

    # ------------------------------------------------------------ 线程这边
    def send(self, msg: dict, conn: _Conn | None = None) -> None:
        conn = conn or self._conn
        if conn is None:
            raise BrowserError("browser", not_connected_text())
        fut = asyncio.run_coroutine_threadsafe(conn.send(msg), conn.loop)
        try:
            fut.result(timeout=SEND_TIMEOUT)
        except Exception as e:
            fut.cancel()
            raise BrowserError("browser", f"发给浏览器扩展失败（{type(e).__name__}）：扩展连接可能刚断开，重试这个任务") from None

    def request_status(self, min_age: float = 10.0) -> None:
        """让扩展再报一次状态（上一次报的不到 min_age 秒就不催，设置页每 5 秒查一次状态）。"""
        if self._conn is not None and (self._status_at is None or time.time() - self._status_at >= min_age):
            try:
                self.send({"type": "status_request"})
            except BrowserError:
                pass

    # ------------------------------------------------------------ rpc（网页文本模型）
    def supports(self, feature: str) -> bool:
        c = self._conn
        return c is not None and feature in (c.hello.get("features") or [])

    def version(self) -> str:
        c = self._conn
        return str((c.hello.get("version") if c is not None else None) or "?")

    def text_problem(self, site: str, label: str) -> str | None:
        """这个网站现在能不能用网页文本模型；不能时返回中文原因（扩展没连上 / 版本太旧要重新加载）。"""
        c = self._conn
        if c is None:
            return not_connected_text()
        h = c.hello or {}
        ver = h.get("version") or "?"
        if RPC_FEATURE not in (h.get("features") or []):
            return reload_text(ver, "还不支持网页文本模型")
        if site not in (h.get("sites") or []):
            return reload_text(ver, f"还不认识 {label} 网页")
        return None

    def rpc(self, method: str, params: dict | None = None, *, timeout: float = RPC_TIMEOUT) -> Any:
        """让扩展做一步页面操作，等它回执（同步，在调用线程里等）。失败抛 BrowserError（中文原因）。"""
        conn = self._conn
        if conn is None:
            raise BrowserError("browser", not_connected_text())
        if RPC_FEATURE not in (conn.hello.get("features") or []):
            raise BrowserError("browser", reload_text(conn.hello.get("version") or "?", "还不支持网页文本模型"))
        rid = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        self._jobs[rid] = q
        try:
            self.send({"type": "rpc", "id": rid, "method": method, "params": dict(params or {})}, conn)
            deadline = time.monotonic() + timeout
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BrowserError("timeout", f"浏览器扩展 {timeout:.0f} 秒没回应（{method}），重试一次")
                try:
                    msg = q.get(timeout=min(left, 0.5))
                except queue.Empty:
                    continue
                if msg.get("type") == "_disconnected":
                    raise BrowserError("browser", f"{msg.get('why', '扩展连接断开了')}，重试一次")
                if msg.get("type") != "rpc_result":
                    continue
                if msg.get("ok"):
                    return msg.get("result")
                kind = msg.get("kind") if msg.get("kind") in KINDS else "browser"
                raise BrowserError(kind, str(msg.get("error") or f"浏览器扩展执行 {method} 失败（没说原因）"))
        finally:
            self._jobs.pop(rid, None)

    def open_urls(self, urls: list[str]) -> None:
        self.send({"type": "open", "urls": list(urls)})

    def generate(self, site: str, label: str, prompt: str, refs: list[Path], *, aspect: str | None,
                 timeout: float, cancel: threading.Event, log: Callable[[str], None],
                 progress: Callable[[int, int], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, poll: float = 0.5,
                 conversation_url: str | None = None, info: dict | None = None) -> list[ExtImage]:
        """info（可选）：扩展在 result 里报的会话情况 {url, continued, note} 填进去（v0.3 起才有）。"""
        conn = self._conn
        if conn is None:
            raise BrowserError("browser", not_connected_text())
        job_id = uuid.uuid4().hex
        q: queue.Queue = queue.Queue()
        self._jobs[job_id] = q
        try:
            msg = {"type": "job", "job_id": job_id, "site": site, "prompt": prompt,
                   "refs": [ref_payload(p) for p in refs], "aspect": aspect, "timeout": timeout,
                   "options": self.options}
            if conversation_url:
                msg["conversation_url"] = conversation_url
            self.send(msg, conn)
            ver = conn.hello.get("version")
            log(f"{label}：任务已发给浏览器扩展{f'（v{ver}）' if ver else ''}，在你的浏览器里出图")
            return self._wait(job_id, q, label, timeout, cancel, log, progress, clock, poll, info)
        finally:
            self._jobs.pop(job_id, None)

    def _wait(self, job_id, q, label, timeout, cancel, log, progress, clock, poll,
              info: dict | None = None) -> list[ExtImage]:
        deadline = clock() + timeout + JOB_GRACE_SECONDS
        chunks: dict[int, dict] = {}
        while True:
            if cancel.is_set():
                self._try_send({"type": "cancel", "job_id": job_id})
                raise JobCanceled()
            if clock() > deadline:
                self._try_send({"type": "cancel", "job_id": job_id})
                raise BrowserError("timeout", f"{label} 超过 {timeout + JOB_GRACE_SECONDS:.0f} 秒浏览器扩展还没回结果，"
                                   "已放弃（浏览器里那条对话还在，可以去看看卡在哪，再重试）")
            try:
                msg = q.get(timeout=poll)
            except queue.Empty:
                continue
            t = msg.get("type")
            if t == "_disconnected":
                raise BrowserError("browser", f"{label} 出图中途{msg.get('why', '扩展连接断开了')}，重试这个任务")
            if t == "progress":
                if progress is not None:
                    try:
                        progress(int(msg.get("value") or 0), int(msg.get("max") or 100))
                    except (TypeError, ValueError):
                        pass
            elif t == "log":
                log(str(msg.get("message", "")))
            elif t == "image":
                self._add_chunk(chunks, msg)
            elif t == "error":
                kind = msg.get("kind") if msg.get("kind") in KINDS else "browser"
                raise BrowserError(kind, str(msg.get("message") or f"{label} 出图失败（扩展没说原因）"))
            elif t == "result":
                if info is not None and isinstance(msg.get("conversation"), dict):
                    info.update(msg["conversation"])
                return self._assemble(chunks, int(msg.get("count") or 0), label)

    def _try_send(self, msg: dict) -> None:
        try:
            self.send(msg)
        except BrowserError:
            pass

    @staticmethod
    def _add_chunk(chunks: dict[int, dict], msg: dict) -> None:
        i = int(msg.get("index") or 0)
        slot = chunks.setdefault(i, {"parts": {}, "total": int(msg.get("total") or 1),
                                     "mime": msg.get("mime") or "", "src": msg.get("src") or "",
                                     "via": msg.get("via") or "fetch"})
        slot["parts"][int(msg.get("seq") or 0)] = str(msg.get("data") or "")

    @staticmethod
    def _assemble(chunks: dict[int, dict], count: int, label: str) -> list[ExtImage]:
        out: list[ExtImage] = []
        for i in sorted(chunks):
            slot = chunks[i]
            parts = slot["parts"]
            if len(parts) != slot["total"]:
                raise BrowserError("browser", f"{label} 第 {i + 1} 张图只收到 {len(parts)}/{slot['total']} 块，"
                                   "传输不完整，重试这个任务")
            try:
                data = base64.b64decode("".join(parts[k] for k in sorted(parts)))
            except Exception:
                raise BrowserError("browser", f"{label} 第 {i + 1} 张图的数据不是合法的 base64") from None
            out.append(ExtImage(data, slot["mime"], slot["src"], slot["via"]))
        if count and len(out) != count:
            raise BrowserError("browser", f"{label} 扩展说出了 {count} 张图，只收到 {len(out)} 张")
        if not out:
            raise BrowserError("no_image", f"{label} 浏览器扩展没有传回图片")
        return out


def decode(img: ExtImage, label: str):
    """校验扩展传回来的图（Python 这边解得开才算），返回 (宽, 高, 格式)。"""
    try:
        im = Image.open(io.BytesIO(img.data))
        im.load()
    except Exception as e:
        raise BrowserError("page", f"{label} 浏览器扩展传回来的不是能解开的图片（{img.mime or '未知类型'}，"
                           f"{len(img.data)} 字节，{type(e).__name__}）：{excerpt(img.src, 120)}") from None
    return im.width, im.height, (im.format or "PNG").upper()


def reload_text(version: str, why: str) -> str:
    return (f"浏览器扩展需要重新加载：现在连着的是 v{version}，{why}。在 chrome://extensions（Edge 是 edge://extensions）"
            "里找到「AI Studio 网页出图桥」点「重新加载」（转圈箭头），再重试")


def conversation_reload_text(version: str) -> str:
    return (f"浏览器扩展 v{version} 不支持续接网页出图会话，这次新开对话：在 chrome://extensions（Edge 是 "
            "edge://extensions）里把「AI Studio 网页出图桥」重新加载一下，就能复用会话")


def not_connected_text() -> str:
    return f"浏览器扩展没有连上 studio：{EXT_INSTALL_HINT}；装好后保持那个浏览器开着，再重试这个任务"
