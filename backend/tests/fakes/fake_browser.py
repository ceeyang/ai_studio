"""假的网页（PageDriver）：按假时钟推进的脚本，模拟 ChatGPT / Gemini 页面的出图过程。

用法：FakePage(spec) 默认已登录、有输入框；after_submit([(秒, 动作), ...]) 安排提交后
第几秒发生什么（出现停止按钮、出半成品图、出完图、回复文字……）。sleep() 推进时钟并执行到点的动作。
"""
from __future__ import annotations

import io
from contextlib import contextmanager
from pathlib import Path

from PIL import Image

from backend.engines.browser.driver import ImageInfo
from backend.engines.browser.sites.base import SiteSpec


def png(w: int, h: int, color=(200, 80, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakePage:
    def __init__(self, spec: SiteSpec, *, logged_in: bool = True, has_file_input: bool = True,
                 has_upload_menu: bool = True, prompt_box: bool = True):
        self.spec = spec
        self.t = 0.0
        self.calls: list[tuple] = []
        self.cookies = set(spec.session_cookies[:1]) if logged_in else set()
        self.shown: set[str] = set()
        if prompt_box:
            self.shown.add(spec.prompt_box)
        self.shown.add(spec.send_button)
        if not logged_in:
            self.shown.add(spec.login_wall)
        self.has_file_input = has_file_input
        self.has_upload_menu = has_upload_menu
        self.attachments = 0
        self.typed = ""
        self.reply = ""
        self.alert = ""
        self.results: list[ImageInfo] = []
        self.mine: list[ImageInfo] = []
        self.blobs: dict[str, bytes] = {}
        self.downloads: dict[str, bytes] = {}
        self.fetch_fails: set[str] = set()
        self._script: list[tuple[float, callable]] = []
        self._submitted_at: float | None = None
        self.url_now = "about:blank"

    # ------------------------------------------------------------ 编排
    def after_submit(self, steps):
        self._script = sorted(steps, key=lambda x: x[0])
        return self

    def show(self, sel):
        self.shown.add(sel)

    def hide(self, sel):
        self.shown.discard(sel)

    def add_result(self, src: str, w: int = 1024, h: int = 1536, data: bytes | None = None, alt: str = ""):
        self.results.append(ImageInfo(src, w, h, True, alt))
        self.blobs[src] = data if data is not None else png(w, h)

    def finish(self, src: str, w: int = 1024, h: int = 1536):
        """出完：停止按钮消失，半成品换成最终图。"""
        self.hide(self.spec.stop_button)
        self.results = []
        self.add_result(src, w, h)

    def _tick(self):
        if self._submitted_at is None:
            return
        while self._script and self._submitted_at + self._script[0][0] <= self.t:
            _, fn = self._script.pop(0)
            fn(self)

    def clock(self) -> float:
        return self.t

    @contextmanager
    def factory(self, spec):
        self.calls.append(("open_tab", spec.key))
        yield self

    # ------------------------------------------------------------ PageDriver
    def goto(self, url):
        self.calls.append(("goto", url))
        self.url_now = url

    def url(self):
        return self.url_now

    def visible(self, selector):
        return selector in self.shown

    def count(self, selector):
        if selector == self.spec.attachment:
            return self.attachments
        return 1 if selector in self.shown else 0

    def enabled(self, selector):
        return selector in self.shown

    def click(self, selector):
        self.calls.append(("click", selector))
        if selector == self.spec.send_button and selector in self.shown:
            self._submitted_at = self.t
            return True
        if selector == self.spec.stop_button and selector in self.shown:
            self.hide(selector)
            return True
        return selector in self.shown

    def type_text(self, selector, text):
        if selector not in self.shown:
            return False
        self.typed = text
        self.calls.append(("type", text))
        return True

    def press(self, selector, key):
        self.calls.append(("press", key))
        return False

    def set_files(self, selector, paths):
        if not self.has_file_input:
            return False
        self.calls.append(("set_files", [Path(p).name for p in paths]))
        self.attachments += len(paths)
        self.mine += [ImageInfo(f"blob:ref{i}", 512, 512, True) for i in range(len(paths))]
        return True

    def choose_files(self, click_selectors, paths):
        if not self.has_upload_menu:
            return False
        self.calls.append(("choose_files", [Path(p).name for p in paths]))
        self.attachments += len(paths)
        return True

    def paste_files(self, selector, paths):
        self.calls.append(("paste_files", [Path(p).name for p in paths]))
        return False

    def text(self, selector):
        if selector == self.spec.assistant_turn:
            return self.reply
        if selector == self.spec.alert:
            return self.alert
        return ""

    def images(self, selector):
        if selector == self.spec.result_images:
            return list(self.results)
        if selector == self.spec.user_images:
            return list(self.mine)
        return []

    def fetch(self, src):
        self.calls.append(("fetch", src))
        if src in self.fetch_fails or src not in self.blobs:
            raise RuntimeError("HTTP 403")
        return self.blobs[src]

    def download(self, image_src, button_selector):
        self.calls.append(("download", image_src))
        return self.downloads.get(image_src)

    def cookie_names(self, url):
        return set(self.cookies)

    def sleep(self, seconds):
        self.t += seconds
        self._tick()
