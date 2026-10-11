"""执行网页出图 / 出视频的 EngineCall：每个网站一把锁、一个标签页，出完落盘（图按要求的画幅居中裁切，
视频用 ffprobe 校验后原样存 mp4；网页出视频只走浏览器扩展）。

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
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from PIL import Image

from backend.engines.base import EngineCall
from backend.engines.browser import conversations as conv
from backend.engines.browser import flow
from backend.engines.browser.driver import PageDriver, PlaywrightDriver
from backend.engines.browser.errors import EXT_DIR, BrowserError
from backend.engines.browser.extension import (CONVERSATION_FEATURE, RPC_FEATURE, VIDEO_FEATURE, ExtensionHub,
                                               conversation_reload_text, decode)
from backend.engines.browser.session import BrowserSession, cookie_names_for
from backend.engines.browser.sites import SITES
from backend.engines.browser.sites.base import SiteSpec
from backend.queue.errors import JobCanceled
from backend.queue.trace import annotate_last

EXT = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
ASPECT_TOLERANCE = 0.015   # 宽高比差 1.5% 以内不裁
VIDEO_CONTAINERS = ("mp4", "mov", "matroska", "webm")   # ffprobe format_name 里有这些才算视频文件


def probe_video(ffprobe: str, path: Path) -> dict:
    """读视频的宽高、时长、有没有音轨；读不出来（不是视频、文件坏了）抛 ValueError。"""
    try:
        p = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                            "stream=codec_type,width,height:format=format_name,duration", "-of", "json", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ValueError(f"ffprobe 跑不起来：{e}") from None
    try:
        info = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        info = {}
    streams = info.get("streams") or []
    v = next((st for st in streams if st.get("codec_type") == "video"), None)
    if p.returncode != 0 or v is None:
        raise ValueError((p.stderr or "没有视频流").strip()[-300:])
    fmt = str((info.get("format") or {}).get("format_name") or "")
    if not any(x in fmt for x in VIDEO_CONTAINERS):   # 图片也会被 ffprobe 当成一帧的「视频流」
        raise ValueError(f"容器格式是 {fmt or '未知'}，不是视频文件")
    try:
        dur = float((info.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"width": int(v.get("width") or 0), "height": int(v.get("height") or 0), "duration": dur,
            "audio": any(st.get("codec_type") == "audio" for st in streams)}


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
                 poll: float = 1.0, hub: ExtensionHub | None = None, ffprobe: str = "ffprobe"):
        self.session = session
        self.ffprobe = ffprobe
        self.sites = dict(sites or SITES)
        self._locks = {k: threading.Lock() for k in self.sites}
        self._page_factory = page_factory or self._playwright_page
        self.poll = poll
        cfg = session.cfg
        self.hub = hub or ExtensionHub({"reuse_conversation": cfg.reuse_conversation,
                                        "reuse_user_tabs": cfg.reuse_user_tabs,
                                        "activate_tab": cfg.activate_tab})
        # 网页会话记录（ConversationBook，build_services 里装上）：没有就每次新开对话、不记
        self.conversations = None

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
            # 连着的是老扩展：v0.1 没有 rpc（网页文本模型用不了），v0.2 没有 video（不能网页出视频）。
            # 要在扩展管理页重新加载（续接会话的能力另见 conversation）
            feats = info.get("features") or []
            missing = [x for x in (RPC_FEATURE, VIDEO_FEATURE) if x not in feats] if info["connected"] else []
            needs_reload = bool(missing)
            return {"transport": "extension", "running": info["connected"], "connected": info["connected"],
                    "extension": info, "extension_dir": str(EXT_DIR), "sites": sites,
                    "needs_reload": needs_reload, "missing_features": missing,
                    "conversation": self.conversation_support(),
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

    # ------------------------------------------------------------ 会话续接
    def conversation_support(self) -> dict:
        """出图能不能续接网页会话：cdp 能；extension 要扩展报了 conversation 能力（v0.3 起）。"""
        if self.transport != "extension":
            return {"supported": True, "hint": None}
        if not self.hub.connected:
            return {"supported": False, "hint": None}
        if self.hub.supports(CONVERSATION_FEATURE):
            return {"supported": True, "hint": None}
        return {"supported": False, "hint": conversation_reload_text(self.hub.version())}

    def _plan(self, spec: SiteSpec, log: Callable[[str], None]) -> conv.Plan | None:
        book = self.conversations
        scope = conv.current_scope()
        if book is None or scope is None or not scope.for_images:
            return None
        plan = book.plan(spec.key, scope)
        if self.transport == "extension" and not self.hub.supports(CONVERSATION_FEATURE):
            # 旧扩展：没法续接、也报不回对话地址——照旧新开对话，提示重新加载扩展
            why = conversation_reload_text(self.hub.version())
            log(f"{spec.label}：{why}")
            plan.fallback(why)
            plan.scope = None     # 读不到对话地址，不记
            return plan
        if plan.continuing:
            log(f"{spec.label}：续接之前的会话 {plan.url}（已发过 {plan.turns} 轮）")
        elif plan.reason:
            log(f"{spec.label}：新开对话（{plan.reason}）")
        return plan

    # ------------------------------------------------------------ 出图
    def run(self, call: EngineCall, *, run_dir: Path, cancel: threading.Event,
            on_progress: Callable[[int, int], None] | None = None,
            log: Callable[[str], None] | None = None) -> list[Path]:
        log = log or (lambda m: None)
        req = call.workflow
        video = call.output_kind == "video"
        spec = self.sites.get(req.get("site", ""))
        field = "result_videos" if video else "result_images"
        if spec is None or not getattr(spec, field):
            can = [k for k, s in self.sites.items() if getattr(s, field)]
            raise BrowserError("browser", f"网站 {req.get('site')!r} 不能{'出视频' if video else '出图'}，"
                                          f"可选：{', '.join(can)}")
        if video and self.transport != "extension":
            raise BrowserError("browser", "网页出视频只支持浏览器扩展（config.yaml 的 browser.transport 要是 extension）")
        prefix = conv.CONTINUE_VIDEO_PREFIX if video else conv.CONTINUE_IMAGE_PREFIX
        plan = self._plan(spec, log)
        if plan is not None and plan.continuing:
            # 在已有对话里接着发：提示词照样完整，开头说明这是一张新图 / 一段新视频，不是改上一个
            req = {**req, "prompt": f"{prefix} {req['prompt']}", "conversation_url": plan.url}
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
        page: dict = {}
        with self.site_lock(spec.key, spec.label, log, cancel):
            if video:
                extra = {"conversation_url": req["conversation_url"]} if req.get("conversation_url") else {}
                items = self.hub.generate(spec.key, spec.label, req["prompt"], refs, aspect=req.get("aspect"),
                                          timeout=timeout, cancel=cancel, log=log, progress=on_progress,
                                          info=page, kind="video", **extra)
            elif self.transport == "extension":
                got = self._via_extension(spec, req, refs, timeout, cancel, log, on_progress, page)
            else:
                with self._page_factory(spec) as driver:
                    got = flow.generate(driver, spec, req["prompt"], refs, timeout=timeout, cancel=cancel,
                                        log=log, progress=on_progress, poll=self.poll,
                                        conversation_url=req.get("conversation_url"), info=page)
        if plan is not None:
            self._record(plan, spec, page, log, prefix)
        if video:
            return self._save_video(items, run_dir, spec, log)
        return self._save(got, run_dir, spec, req.get("aspect"), bool(req.get("fit_aspect", True)), log)

    def _save_video(self, items: list, run_dir: Path, spec: SiteSpec, log: Callable[[str], None]) -> list[Path]:
        """扩展传回来的视频：ffprobe 读得出视频才算，原样存 mp4（画幅已经在页面上选了 / 写进提示词，不裁）。"""
        paths: list[Path] = []
        for i, item in enumerate(items):
            out = run_dir / f"{spec.key}_{i:02d}.mp4"
            out.write_bytes(item.data)
            try:
                info = probe_video(self.ffprobe, out)
            except ValueError as e:
                raise BrowserError("page", f"{spec.label} 浏览器扩展传回来的不是能读的视频（{item.mime or '未知类型'}，"
                                           f"{len(item.data)} 字节）：{e}") from None
            how = "在页面里带登录态" if item.via == "page" else "由扩展直接"
            log(f"{spec.label}：视频 {info['width']}×{info['height']}，{info['duration']:.1f} 秒，"
                f"{'有声' if info['audio'] else '无声'}（{how}取视频地址）")
            paths.append(out)
        if len(paths) > 1:
            log(f"{spec.label} 一次出了 {len(paths)} 段视频，候选用第一段，其余留在任务目录 {run_dir}")
        if not paths:
            raise BrowserError("no_image", f"{spec.label} 没有出视频")
        return paths

    def _record(self, plan: conv.Plan, spec: SiteSpec, page: dict, log: Callable[[str], None],
                prefix: str = conv.CONTINUE_IMAGE_PREFIX) -> None:
        if plan.scope is None:   # 旧扩展：没续接、读不到地址
            info = {"mode": "new", "url": None, "reason": plan.reason or None, "site": spec.key,
                    "scope": None, "turns": 1}
        else:
            info = self.conversations.record(plan, spec, page.get("url"), continued=page.get("continued"),
                                             note=page.get("note") or "")
            log(conv.describe(info, spec.label))
            if info.get("mode") == "continue":
                info["prompt_prefix"] = prefix
        annotate_last("engine", {"conversation": info})

    def _via_extension(self, spec: SiteSpec, req: dict, refs: list[Path], timeout: float,
                       cancel: threading.Event, log: Callable[[str], None],
                       on_progress: Callable[[int, int], None] | None,
                       page: dict | None = None) -> list[flow.GeneratedImage]:
        extra = {"conversation_url": req["conversation_url"]} if req.get("conversation_url") else {}
        imgs = self.hub.generate(spec.key, spec.label, req["prompt"], refs, aspect=req.get("aspect"),
                                 timeout=timeout, cancel=cancel, log=log, progress=on_progress,
                                 info=page, **extra)
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
