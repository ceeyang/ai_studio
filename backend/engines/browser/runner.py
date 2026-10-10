"""执行网页出图的 EngineCall：每个网站一把锁、一个标签页，出完落盘、按要求的画幅居中裁切。

任务跑在队列的「网页通道」线程里（不占 GPU worker，也不触发 ComfyUI 释放显存），每个网站一条
串行通道；这里的锁再兜一层，防止 CLI 或别的入口同时操作同一个标签页。
"""
from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from PIL import Image

from backend.engines.base import EngineCall
from backend.engines.browser import flow
from backend.engines.browser.driver import PageDriver, PlaywrightDriver
from backend.engines.browser.errors import BrowserError
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
                 poll: float = 1.0):
        self.session = session
        self.sites = dict(sites or SITES)
        self._locks = {k: threading.Lock() for k in self.sites}
        self._page_factory = page_factory or self._playwright_page
        self.poll = poll

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
    def login_status(self) -> dict[str, bool]:
        """按 cookie 判断每个网站是否登录（不开页面）。浏览器没在跑时抛 BrowserError。"""
        cookies = self.session.cookies()
        return {k: any(c in cookie_names_for(cookies, s.host) for c in s.session_cookies)
                for k, s in self.sites.items()}

    def open_login(self) -> dict:
        urls = [s.home for s in self.sites.values()]
        launched = self.session.open_login(urls)
        return {"launched": launched, "urls": urls, "endpoint": self.session.endpoint,
                "user_data_dir": str(self.session.cfg.user_data_dir)}

    # ------------------------------------------------------------ 出图
    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            log: Callable[[str], None] | None = None) -> list[Path]:
        log = log or (lambda m: None)
        req = call.workflow
        spec = self.sites.get(req.get("site", ""))
        if spec is None:
            raise BrowserError("browser", f"未知网站 {req.get('site')!r}，可选：{', '.join(self.sites)}")
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "workflow.api.json").write_text(
            json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "call.json").write_text(json.dumps(
            {"engine": call.engine, "runner": call.runner, "output_kind": call.output_kind,
             "model_family": call.model_family, "meta": call.meta,
             "dropped_refs": call.dropped_refs},
            ensure_ascii=False, indent=2, default=str), encoding="utf-8")

        lock = self._locks[spec.key]
        if not lock.acquire(blocking=False):
            log(f"{spec.label} 的标签页正被另一个任务用着，排队等它")
            while not lock.acquire(timeout=1):
                if cancel.is_set():
                    raise JobCanceled()
        try:
            with self._page_factory(spec) as driver:
                got = flow.generate(driver, spec, req["prompt"], [Path(p) for p in req.get("refs") or []],
                                    timeout=float(req.get("timeout") or 300), cancel=cancel, log=log,
                                    progress=on_progress, poll=self.poll)
        finally:
            lock.release()
        return self._save(got, run_dir, spec, req.get("aspect"), bool(req.get("fit_aspect", True)), log)

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
