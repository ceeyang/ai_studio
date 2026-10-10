"""页面操作的薄封装。flow.py 只通过 PageDriver 操作页面，单测换成假的就能跑完整流程。

PlaywrightDriver 是真实实现：同步 API，必须在创建它的那个线程里用（runner 每次出图在任务线程里
现连现断，不跨线程）。所有方法找不到元素时返回 False / 空值，不抛 Playwright 的异常。
"""
from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ImageInfo:
    src: str
    width: int          # naturalWidth
    height: int         # naturalHeight
    complete: bool      # 加载完成
    alt: str = ""       # 网站给的说明（如「Generated image 1」），同一张图的副本按它和尺寸去重


class PageDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def url(self) -> str: ...
    def visible(self, selector: str) -> bool: ...
    def count(self, selector: str) -> int: ...
    def enabled(self, selector: str) -> bool: ...
    def click(self, selector: str) -> bool: ...
    def type_text(self, selector: str, text: str) -> bool: ...
    def press(self, selector: str, key: str) -> bool: ...
    def set_files(self, selector: str, paths: list[Path]) -> bool: ...
    def choose_files(self, click_selectors: tuple[str, ...], paths: list[Path]) -> bool: ...
    def paste_files(self, selector: str, paths: list[Path]) -> bool: ...
    def text(self, selector: str) -> str: ...
    def images(self, selector: str) -> list[ImageInfo]: ...
    def fetch(self, src: str) -> bytes: ...
    def download(self, image_src: str, button_selector: str) -> bytes | None: ...
    def cookie_names(self, url: str) -> set[str]: ...
    def sleep(self, seconds: float) -> None: ...


_FETCH_JS = """async (src) => {
  const r = await fetch(src, {credentials: 'include'});
  if (!r.ok) throw new Error('HTTP ' + r.status);
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
  return btoa(s);
}"""

# 不按布局判断显不显示（后台标签页里正图也会量出 0×0）；同一张图的副本在 flow 里按 alt+尺寸去重
_IMAGES_JS = """els => els.map(e => ({src: e.currentSrc || e.src || '', w: e.naturalWidth || 0,
                                   h: e.naturalHeight || 0, complete: !!e.complete, alt: e.alt || ''}))"""

_PASTE_JS = """([sel, files]) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const dt = new DataTransfer();
  for (const f of files) {
    const bin = atob(f.b64); const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    dt.items.add(new File([arr], f.name, {type: f.mime}));
  }
  el.focus();
  el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  return true;
}"""


class PlaywrightDriver:
    def __init__(self, page: Any, context: Any):
        self.page = page
        self.context = context

    # ------------------------------------------------------------ 基础
    def _first_visible(self, selector: str):
        try:
            loc = self.page.locator(selector)
            n = loc.count()
            for i in range(min(n, 20)):
                el = loc.nth(i)
                if el.is_visible():
                    return el
        except Exception:
            return None
        return None

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    def url(self) -> str:
        return self.page.url

    def visible(self, selector: str) -> bool:
        return self._first_visible(selector) is not None

    def count(self, selector: str) -> int:
        try:
            return self.page.locator(selector).count()
        except Exception:
            return 0

    def enabled(self, selector: str) -> bool:
        el = self._first_visible(selector)
        try:
            return el is not None and el.is_enabled()
        except Exception:
            return False

    def click(self, selector: str) -> bool:
        el = self._first_visible(selector)
        if el is None:
            return False
        try:
            el.click(timeout=5_000)
            return True
        except Exception:
            return False

    def type_text(self, selector: str, text: str) -> bool:
        el = self._first_visible(selector)
        if el is None:
            return False
        try:
            el.click(timeout=5_000)
            el.fill(text, timeout=10_000)
        except Exception:
            try:  # 有的编辑器不吃 fill，退回逐字插入（text 里不能有换行，换行会被当成回车发送）
                self.page.keyboard.insert_text(text)
            except Exception:
                return False
        try:
            got = el.inner_text(timeout=2_000)
        except Exception:
            return True
        return text[:30].strip() in " ".join(got.split()) or len(got.strip()) >= min(len(text), 30)

    def press(self, selector: str, key: str) -> bool:
        el = self._first_visible(selector)
        if el is None:
            return False
        try:
            el.press(key, timeout=5_000)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 上传
    def set_files(self, selector: str, paths: list[Path]) -> bool:
        try:
            loc = self.page.locator(selector)
            if loc.count() == 0:
                return False
            el = loc.first
            files = [str(p) for p in paths]
            if len(files) == 1 or el.evaluate("e => !!e.multiple"):
                el.set_input_files(files, timeout=15_000)
            else:  # 不支持多选就一张一张传
                for f in files:
                    el.set_input_files(f, timeout=15_000)
                    self.page.wait_for_timeout(1_500)
            return True
        except Exception:
            return False

    def choose_files(self, click_selectors: tuple[str, ...], paths: list[Path]) -> bool:
        if not click_selectors:
            return False
        try:
            for sel in click_selectors[:-1]:
                if self.click(sel):
                    self.page.wait_for_timeout(600)
            with self.page.expect_file_chooser(timeout=8_000) as fc:
                if not self.click(click_selectors[-1]):
                    return False
            fc.value.set_files([str(p) for p in paths], timeout=15_000)
            return True
        except Exception:
            return False

    def paste_files(self, selector: str, paths: list[Path]) -> bool:
        files = [{"name": p.name, "mime": mimetypes.guess_type(p.name)[0] or "image/png",
                  "b64": base64.b64encode(Path(p).read_bytes()).decode()} for p in paths]
        sel = selector.split(",")[0].strip()
        try:
            return bool(self.page.evaluate(_PASTE_JS, [sel, files]))
        except Exception:
            return False

    # ------------------------------------------------------------ 读取
    def text(self, selector: str) -> str:
        try:
            loc = self.page.locator(selector)
            n = loc.count()
            return loc.nth(n - 1).inner_text(timeout=2_000) if n else ""
        except Exception:
            return ""

    def images(self, selector: str) -> list[ImageInfo]:
        try:
            rows = self.page.locator(selector).evaluate_all(_IMAGES_JS)
        except Exception:
            return []
        return [ImageInfo(r["src"], int(r["w"]), int(r["h"]), bool(r["complete"]), str(r.get("alt") or "")) for r in rows]

    def fetch(self, src: str) -> bytes:
        """在页面里 fetch（同源带 cookie、blob: 也能取）；跨域被 CORS 挡住时用浏览器上下文的请求再取一次。"""
        if src.startswith("data:"):
            return base64.b64decode(src.split(",", 1)[1])
        try:
            return base64.b64decode(self.page.evaluate(_FETCH_JS, src))
        except Exception:
            if src.startswith("blob:"):
                raise
        r = self.context.request.get(src, timeout=60_000)
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status}")
        return r.body()

    def download(self, image_src: str, button_selector: str) -> bytes | None:
        """悬停到这张图上，点「下载」按钮，接住浏览器下载的文件。"""
        try:
            esc = image_src.replace("\\", "\\\\").replace('"', '\\"')
            img = self.page.locator(f'img[src="{esc}"]').first
            img.hover(timeout=5_000)
            self.page.wait_for_timeout(500)
            with self.page.expect_download(timeout=90_000) as d:
                if not self.click(button_selector):
                    return None
            path = d.value.path()
            return Path(path).read_bytes() if path else None
        except Exception:
            return None

    def cookie_names(self, url: str) -> set[str]:
        try:
            return {c["name"] for c in self.context.cookies([url])}
        except Exception:
            return set()

    def sleep(self, seconds: float) -> None:
        self.page.wait_for_timeout(int(seconds * 1000))
