"""执行网页出图的 EngineCall：每个网站一把锁、一个标签页，出完落盘、按要求的画幅居中裁切。

任务跑在队列的「网页通道」线程里（不占 GPU worker，也不触发 ComfyUI 释放显存），每个网站一条
串行通道；这里的锁再兜一层，防止 CLI 或别的入口同时操作同一个标签页。

两种 transport（config.yaml browser.transport）：
- extension（默认）：任务发给装在用户平时浏览器里的 browser-extension（ExtensionHub），它在已登录的
  标签页里跑完页面流程、把原图传回来；这里只负责校验、落盘、裁切。不启动任何浏览器。
- cdp（备用）：studio 自己带调试端口启动独立配置目录的浏览器，Playwright 走 CDP 跑 flow.py。
"""
from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from PIL import Image

from backend.engines.base import EngineCall
from backend.engines.browser import flow
from backend.engines.browser.driver import PageDriver, PlaywrightDriver
from backend.engines.browser.errors import EXT_DIR, BrowserError
from backend.engines.browser.extension import RPC_FEATURE, ExtensionHub, decode
from backend.engines.browser.session import BrowserSession, cookie_names_for
from backend.engines.browser.sites import SITES
from backend.engines.browser.sites.base import SiteSpec
from backend.queue.errors import JobCanceled

EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
ASPECT_TOLERANCE = 0.015   # 宽高比差 1.5% 以内不裁


def parse_aspect(aspect: str) -> float:
    w, h = aspect.split(":")
    return float(w) / float(h)


def crop_to_aspect(im: Image.Image, aspect: str) -> Image.Image | None:
    """居中裁到要求的宽高比；本来就差不多时返回 None。网页出图没有尺寸参数，ChatGPT 竖图常是 2:3。"""
    want = parse_aspect(aspect)
    w, h = im.size
    have = w / h
    if abs(have - want) / want <= ASPECT_TOLERANCE:
        return None
    if have > want:   # 太宽，裁左右
        nw = round(h * want)
        x = (w - nw) // 2
        return im.crop((x, 0, x + nw, h))
    nh = round(w / want)
    y = (h - nh) // 2
    return im.crop((0, y, w, y + nh))


class BrowserRunner:
    def __init__(self, session: BrowserSession, sites: dict[str, SiteSpec] | None = None, *,
                 page_factory: Callable[[SiteSpec], AbstractContextManager[PageDriver]] | None = None,
                 poll: float = 1.0, hub: ExtensionHub | None = None):
        self.session = session
        self.sites = dict(sites or SITES)
        self._locks = {k: threading.Lock() for k in self.sites}
        self._page_factory = page_factory or self._playwright_page
        self.poll = poll
        cfg = session.cfg
        self.hub = hub or ExtensionHub({"reuse_conversation": cfg.reuse_conversation,
                                        "reuse_user_tabs": cfg.reuse_user_tabs,
                                        "activate_tab": cfg.activate_tab})

    @property
    def transport(self) -> str:
        return self.session.cfg.transport

    @contextmanager
    def site_lock(self, key: str, label: str, log: Callable[[str], None],
                  cancel: threading.Event | None = None, wait_timeout: float | None = None) -> Iterator[None]:
        """同一个网站同时只跑一件事（出图、网页文本模型共用这把锁）：被占着时在日志里说一声再排队等。
        wait_timeout：最多等这么多秒，超过抛 BrowserError（None = 一直等）。"""
        lock = self._locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            log(f"{label} 的标签页正被另一个任务用着，排队等它")
            waited = 0
            while not lock.acquire(timeout=1):
                waited += 1
                if cancel is not None and cancel.is_set():
                    raise JobCanceled()
                if wait_timeout is not None and waited >= wait_timeout:
                    raise BrowserError("timeout", f"{label} 的标签页被别的任务占了 {waited} 秒还没让出来，已放弃，稍后重试")
        try:
            yield
        finally:
            lock.release()

    # ------------------------------------------------------------ 标签页
    def _playwright_page(self, spec: SiteSpec):
        from contextlib import contextmanager

        @contextmanager
        def cm():
            with self.session.connect() as (_browser, ctx):
                page = next((p for p in ctx.pages if spec.host in (p.url or "")), None)
                if page is None:
                    page = ctx.new_page()
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                yield PlaywrightDriver(page, ctx)
        return cm()

    # ------------------------------------------------------------ 登录
    def login_status(self) -> dict[str, bool | None]:
        """每个网站是否登录。cdp：按 studio 那个浏览器的 cookie（不开页面），没在跑时抛 BrowserError。
        extension：扩展最近一次报的状态（它用 chrome.cookies 看登录 cookie 在不在、再看开着的标签页有没有
        登录按钮），没连上时全是 None。"""
        if self.transport == "extension":
            st = self.hub.site_status()
            return {k: (bool(st[k].get("logged_in")) if k in st else None) for k in self.sites}
        cookies = self.session.cookies()
        return {k: any(c in cookie_names_for(cookies, s.host) for c in s.session_cookies)
                for k, s in self.sites.items()}

    def open_login(self) -> dict:
        urls = [s.home for s in self.sites.values()]
        if self.transport == "extension":
            # 不启动浏览器：让扩展在用户自己的浏览器里打开两个网站，没登录的就地登录
            self.hub.open_urls(urls)
            return {"transport": "extension", "launched": False, "urls": urls}
        launched = self.session.open_login(urls)
        return {"transport": "cdp", "launched": launched, "urls": urls, "endpoint": self.session.endpoint,
                "user_data_dir": str(self.session.cfg.user_data_dir)}

    def status(self) -> dict:
        """设置页和 /api/browser/status 用：不会启动浏览器。"""
        if self.transport == "extension":
            info = self.hub.info()
            st = self.hub.site_status()
            sites = {k: {"label": s.label, "host": s.host, "tabs": st.get(k, {}).get("tabs"),
                         "logged_in": (bool(st[k].get("logged_in")) if k in st else None),
                         "cookie": st.get(k, {}).get("cookie"), "page": st.get(k, {}).get("page")}
                     for k, s in self.sites.items()}
            # 连着的是 v0.1 的扩展（没有 rpc）：网页文本模型用不了，要在扩展管理页重新加载
            needs_reload = bool(info["connected"]) and RPC_FEATURE not in (info.get("features") or [])
            return {"transport": "extension", "running": info["connected"], "connected": info["connected"],
                    "extension": info, "extension_dir": str(EXT_DIR), "sites": sites,
                    "needs_reload": needs_reload,
                    "logged_in": {k: v["logged_in"] for k, v in sites.items()}}
        sess = self.session
        exe = sess.executable()
        running = sess.version() is not None
        logged: dict[str, bool | None] = {k: None for k in self.sites}
        if running:
            try:
                logged.update(self.login_status())
            except Exception:
                pass
        return {"transport": "cdp", "running": running, "executable": str(exe) if exe else None,
                "endpoint": sess.endpoint, "user_data_dir": str(sess.cfg.user_data_dir),
                "logged_in": logged}

    # ------------------------------------------------------------ 出图
    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            log: Callable[[str], None] | None = None) -> list[Path]:
        log = log or (lambda m: None)
        req = call.workflow
        spec = self.sites.get(req.get("site", ""))
        if spec is None or not spec.result_images:
            can = [k for k, s in self.sites.items() if s.result_images]
            raise BrowserError("browser", f"网站 {req.get('site')!r} 不能出图，可选：{', '.join(can)}")
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.api.json").write_text(
            json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "call.json").write_text(json.dumps(
            {"engine": call.engine, "runner": call.runner, "output_kind": call.output_kind,
             "model_family": call.model_family, "meta": call.meta,
             "dropped_refs": call.dropped_refs},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        refs = [Path(p) for p in req.get("refs") or []]
        timeout = float(req.get("timeout") or 300)
        with self.site_lock(spec.key, spec.label, log, cancel):
            if self.transport == "extension":
                got = self._via_extension(spec, req, refs, timeout, cancel, log, on_progress)
            else:
                with self._page_factory(spec) as driver:
                    got = flow.generate(driver, spec, req["prompt"], refs, timeout=timeout, cancel=cancel,
                                        log=log, progress=on_progress, poll=self.poll)
        return self._save(got, run_dir, spec, req.get("aspect"), bool(req.get("fit_aspect", True)), log)

    def _via_extension(self, spec: SiteSpec, req: dict, refs: list[Path], timeout: float,
                       cancel: threading.Event, log: Callable[[str], None],
                       on_progress: Callable[[int, int], None] | None) -> list[flow.GeneratedImage]:
        imgs = self.hub.generate(spec.key, spec.label, req["prompt"], refs, aspect=req.get("aspect"),
                                 timeout=timeout, cancel=cancel, log=log, progress=on_progress)
        got: list[flow.GeneratedImage] = []
        for img in imgs:
            w, h, fmt = decode(img, spec.label)
            how = "在页面里带登录态" if img.via == "page" else "由扩展直接"
            log(f"{spec.label}：原图 {w}×{h} {fmt}（{how}取图片地址）")
            got.append(flow.GeneratedImage(img.data, img.src, img.via, w, h, fmt))
        return got

    @staticmethod
    def _save(got: list[flow.GeneratedImage], run_dir: Path, spec: SiteSpec, aspect: str | None,
              fit: bool, log: Callable[[str], None]) -> list[Path]:
        paths: list[Path] = []
        for i, g in enumerate(got):
            raw = run_dir / f"{spec.key}_{i:02d}_orig{EXT.get(g.format, '.png')}"
            raw.write_bytes(g.data)
            final = raw
            if fit and aspect:
                im = Image.open(io.BytesIO(g.data))
                cropped = crop_to_aspect(im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im,
                                         aspect)
                if cropped is not None:
                    final = run_dir / f"{spec.key}_{i:02d}.png"
                    cropped.save(final)
                    log(f"{spec.label}：原图 {im.width}×{im.height} 不是 {aspect}，居中裁成 "
                        f"{cropped.width}×{cropped.height}")
            paths.append(final)
        if len(paths) > 1:
            log(f"{spec.label} 一次出了 {len(paths)} 张，候选用第一张，其余留在任务目录 {run_dir}")
        if not paths:
            raise BrowserError("no_image", f"{spec.label} 没有出图")
        return paths
