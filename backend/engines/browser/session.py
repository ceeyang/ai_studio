"""studio 管的那个浏览器：找程序、带调试端口启动独立配置目录、CDP 连接、读登录 cookie。

- Chrome 136 起默认配置目录不许开远程调试，所以用 config 里的 browser.user_data_dir（默认仓库下
  browser-profile/，不进 git）。用户在这个窗口里登录一次 ChatGPT / Gemini，登录态留在目录里。
- 浏览器是独立进程（脱离后端进程组），后端重启它也不关；端口已经在听就直接复用。
- 不用 Playwright 的 launch（会加 --enable-automation，Google 登录会拦），而是自己按普通方式启动，
  Playwright 只用 connect_over_cdp 连上去。
- doctor 的登录检查只走 CDP 的 Storage.getCookies，不开页面、不需要 Playwright。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from backend.config import BrowserCfg
from backend.engines.browser.errors import BrowserError


def _windows_candidates() -> list[Path]:
    out: list[Path] = []
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    return out


def find_executable(cfg: BrowserCfg) -> Path | None:
    """config 写了就用它；否则先找 Chrome，再找 Edge。"""
    if cfg.executable:
        return Path(cfg.executable) if Path(cfg.executable).is_file() else None
    if sys.platform == "win32":
        for p in _windows_candidates():
            if p.is_file():
                return p
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser",
                 "microsoft-edge", "msedge"):
        w = shutil.which(name)
        if w:
            return Path(w)
    mac = [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
           Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    return next((p for p in mac if p.is_file()), None)


class BrowserSession:
    def __init__(self, cfg: BrowserCfg):
        self.cfg = cfg

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.cfg.cdp_port}"

    def executable(self) -> Path | None:
        return find_executable(self.cfg)

    # ------------------------------------------------------------ 端口
    def version(self) -> dict | None:
        """调试端口在听就返回 /json/version（Browser、webSocketDebuggerUrl），否则 None。"""
        try:
            r = httpx.get(self.endpoint + "/json/version", timeout=2, trust_env=False)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def port_taken(self) -> bool:
        """端口被占用（不管是不是浏览器）。"""
        try:
            with socket.create_connection(("127.0.0.1", self.cfg.cdp_port), timeout=1):
                return True
        except OSError:
            return False

    # ------------------------------------------------------------ 启动
    def launch_args(self, urls: list[str] = ()) -> list[str]:
        exe = self.executable()
        if exe is None:
            raise BrowserError("browser", "找不到 Chrome 或 Edge：装一个，或在 config.yaml 的 "
                               "browser.executable 写上浏览器程序的完整路径")
        args = [str(exe), f"--remote-debugging-port={self.cfg.cdp_port}",
                f"--user-data-dir={Path(self.cfg.user_data_dir).resolve()}",
                "--no-first-run", "--no-default-browser-check",
                # 出图的标签页常在后台，别让浏览器把它节流到停住
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding"]
        if self.cfg.headless:
            args.append("--headless=new")
        return args + list(urls)

    def ensure_running(self, urls: list[str] = ()) -> bool:
        """调试端口没在听就启动浏览器（带上 urls 打开），返回是否新启动的。已经在跑时不打开 urls。"""
        if self.version() is not None:
            return False
        if self.port_taken():
            raise BrowserError("browser", f"端口 {self.cfg.cdp_port} 被别的程序占用了，不是浏览器调试端口；"
                               "在 config.yaml 的 browser.cdp_port 换一个")
        args = self.launch_args(urls)
        Path(self.cfg.user_data_dir).mkdir(parents=True, exist_ok=True)
        flags = 0
        if sys.platform == "win32":  # 脱离后端进程组：后端重启/被杀时浏览器和登录态不受影响
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        deadline = time.monotonic() + self.cfg.launch_timeout
        while time.monotonic() < deadline:
            if self.version() is not None:
                return True
            time.sleep(0.5)
        raise BrowserError("browser", f"浏览器启动后 {self.cfg.launch_timeout:.0f} 秒内调试端口 "
                           f"{self.cfg.cdp_port} 没有响应。可能这个配置目录（{self.cfg.user_data_dir}）"
                           "已经被一个没开调试端口的浏览器窗口占着——把那个窗口关掉再试")

    def open_tabs(self, urls: list[str]) -> None:
        for u in urls:
            r = httpx.put(f"{self.endpoint}/json/new?{quote(u, safe=':/?&=')}", timeout=5,
                          trust_env=False)
            r.raise_for_status()

    def open_login(self, urls: list[str]) -> bool:
        """打开登录页：没在跑就带着这些网址启动，在跑就新开标签页。返回是否新启动的。"""
        launched = self.ensure_running(urls)
        if not launched:
            self.open_tabs(urls)
        return launched

    # ------------------------------------------------------------ CDP
    def cookies(self) -> list[dict]:
        """浏览器里所有 cookie（Storage.getCookies，走浏览器级 WebSocket）。没在跑时抛 BrowserError。"""
        from websockets.sync.client import connect as ws_connect

        v = self.version()
        if v is None:
            raise BrowserError("browser", "浏览器没在运行")
        url = v.get("webSocketDebuggerUrl")
        try:
            ws = ws_connect(url, open_timeout=5, max_size=None, proxy=None)
        except TypeError:  # 旧版 websockets 没有 proxy 参数
            ws = ws_connect(url, open_timeout=5, max_size=None)
        with ws:
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                msg = json.loads(ws.recv(timeout=10))
                if msg.get("id") == 1:
                    if "error" in msg:
                        raise BrowserError("browser", f"读 cookie 失败：{msg['error']}")
                    return msg.get("result", {}).get("cookies", [])
        raise BrowserError("browser", "读 cookie 超时")

    @contextmanager
    def connect(self) -> Iterator[tuple[object, object]]:
        """Playwright 连上浏览器，给出 (playwright, 默认上下文)。退出时只断开连接，不关浏览器。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserError("browser", "没装 playwright：在仓库根目录运行 uv sync 后重启服务") from None
        self.ensure_running()
        pw = sync_playwright().start()
        try:
            try:
                browser = pw.chromium.connect_over_cdp(self.endpoint, timeout=30_000)
            except Exception as e:
                raise BrowserError("browser", f"连不上浏览器调试端口 {self.endpoint}：{e}") from None
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            yield browser, ctx
        finally:
            pw.stop()  # 连上去的浏览器（connect_over_cdp）只断开，不会被关掉


def cookie_names_for(cookies: list[dict], host: str) -> set[str]:
    """某个网站能带上的 cookie 名（域名匹配：.google.com 对 gemini.google.com 也算）。"""
    out = set()
    for c in cookies:
        dom = str(c.get("domain", "")).lstrip(".")
        if host == dom or host.endswith("." + dom):
            out.add(c.get("name"))
    return out
