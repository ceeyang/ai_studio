"""网页文本模型的页面流程：在 ChatGPT / Gemini / Claude 网页里开新对话 → （看图时）传图 → 输提示词 → 发送 →
等回复写完（不在生成、文字连续几秒不变）→ 读最后一条回复的全文。

和出图不同，这个流程在 studio 这边跑：每一步通过浏览器扩展的 rpc 做一次页面操作（ExtTabDriver），选择器
也从这边发过去（sites/*.py 的 prompt_box / send_button / stop_button / busy_marker / text_reply …）。
所以网站改版时改 Python 的选择器、重启 studio 就行，不用重新加载扩展。

只通过 ChatDriver 操作页面，单测换成假的就能跑完整流程。出错抛 BrowserError（中文原因）。
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from backend.engines.browser.errors import BrowserError, excerpt
from backend.engines.browser.extension import ExtensionHub, ref_payload
from backend.engines.browser.sites.base import SiteSpec

Log = Callable[[str], None]

# 标签页被跳到这些地址上 = 没登录
LOGIN_URL_HINTS = ("auth.openai.com", "auth0.openai.com", "accounts.google.com", "claude.ai/login")
START_TIMEOUT = 90.0       # 发出去这么久还没开始回复（没在生成、也没有回复文字）就判失败
SETTLE_SECONDS = 3.0       # 不在生成、回复文字连续这么久不变才算写完
REPORT_EVERY = 20.0        # 等回复时每隔多久在任务日志里写一次进度
SEND_WAIT = 30.0           # 发送按钮是灰的（图片还在处理）最多等多久


class ChatDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def url(self) -> str: ...
    def visible(self, selector: str) -> bool: ...
    def count(self, selector: str) -> int: ...
    def enabled(self, selector: str) -> bool: ...
    def click(self, selector: str) -> bool: ...
    def type_text(self, selector: str, text: str) -> bool: ...
    def press(self, selector: str, key: str) -> bool: ...
    def set_files(self, selector: str, files: list[dict], near: str) -> bool: ...
    def paste_files(self, selector: str, files: list[dict]) -> bool: ...
    def composer_images(self, near: str) -> int: ...
    def text(self, selector: str) -> str: ...
    def reply_text(self, selector: str, exclude: str) -> tuple[str, int]: ...
    def sleep(self, seconds: float) -> None: ...


class ExtTabDriver:
    """ChatDriver 的扩展实现：每个方法一次 rpc（扩展里是一次 chrome.scripting.executeScript）。"""

    def __init__(self, hub: ExtensionHub, tab_id: int, *, sleep: Callable[[float], None] = time.sleep):
        self.hub = hub
        self.tab_id = tab_id
        self._sleep = sleep

    def _dom(self, op: str, *args: Any) -> Any:
        return self.hub.rpc("dom", {"tab_id": self.tab_id, "op": op, "args": list(args)})

    def goto(self, url: str) -> None:
        self.hub.rpc("goto", {"tab_id": self.tab_id, "url": url}, timeout=120.0)

    def url(self) -> str:
        return str(self.hub.rpc("url", {"tab_id": self.tab_id}) or "")

    def visible(self, selector: str) -> bool:
        return bool(selector) and bool(self._dom("visible", selector))

    def count(self, selector: str) -> int:
        try:
            return int(self._dom("count", selector) or 0)
        except (TypeError, ValueError):
            return 0

    def enabled(self, selector: str) -> bool:
        return bool(self._dom("enabled", selector))

    def click(self, selector: str) -> bool:
        return bool(self._dom("click", selector))

    def type_text(self, selector: str, text: str) -> bool:
        return bool(self._dom("typeText", selector, text))

    def press(self, selector: str, key: str) -> bool:
        return bool(self._dom("press", selector, key))

    def set_files(self, selector: str, files: list[dict], near: str) -> bool:
        return bool(self._dom("setFiles", selector, files, near))

    def paste_files(self, selector: str, files: list[dict]) -> bool:
        return bool(self._dom("pasteFiles", selector, files))

    def composer_images(self, near: str) -> int:
        try:
            return int(self._dom("composerImages", near) or 0)
        except (TypeError, ValueError):
            return 0

    def text(self, selector: str) -> str:
        r = self._dom("text", selector, "")
        return r if isinstance(r, str) else ""

    def reply_text(self, selector: str, exclude: str) -> tuple[str, int]:
        r = self._dom("replyText", selector, exclude)
        if not isinstance(r, dict):
            return "", 0
        try:
            return str(r.get("text") or ""), int(r.get("count") or 0)
        except (TypeError, ValueError):
            return str(r.get("text") or ""), 0

    def sleep(self, seconds: float) -> None:
        self._sleep(seconds)


def _match(patterns, text: str) -> bool:
    return bool(text) and any(p.search(text) for p in patterns)


def site_file(spec: SiteSpec) -> str:
    return f"backend/engines/browser/sites/{spec.key}.py（和 browser-extension/sites/{spec.key}.js）"


def not_logged_in(spec: SiteSpec) -> BrowserError:
    return BrowserError("not_logged_in", f"{spec.label} 网页没有登录：在装了扩展的浏览器里打开 {spec.host} 登录"
                                         "（只需一次），再重试")


class ChatFlow:
    def __init__(self, d: ChatDriver, spec: SiteSpec, *, log: Log, clock: Callable[[], float] = time.monotonic,
                 poll: float = 1.0, settle: float = SETTLE_SECONDS, start_timeout: float = START_TIMEOUT,
                 report_every: float = REPORT_EVERY):
        self.d, self.spec, self.log, self.clock, self.poll = d, spec, log, clock, poll
        self.settle, self.start_timeout, self.report_every = settle, start_timeout, report_every
        self.reply_sel = spec.text_reply or spec.assistant_turn
        self.reply_ex = spec.text_exclude or spec.reply_exclude
        self.label = f"{spec.label} 网页"

    # ------------------------------------------------------------ 步骤
    def check_login(self) -> None:
        here = self.d.url()
        if any(h in here for h in LOGIN_URL_HINTS) or self.d.visible(self.spec.login_wall):
            raise not_logged_in(self.spec)

    def open_chat(self, fresh: bool) -> None:
        s = self.spec
        if not fresh:
            self.d.goto(s.new_chat_url)
        start = self.clock()
        while True:
            self.check_login()
            if self.d.visible(s.prompt_box):
                if s.dismiss_buttons and self.d.visible(s.dismiss_buttons):
                    self.d.click(s.dismiss_buttons)
                return
            if self.clock() - start > s.ready_timeout:
                raise BrowserError("page", f"{self.label} {s.ready_timeout:.0f} 秒内没出现输入框（当前地址 {self.d.url()}）。"
                                           f"网络不通，或网站改版了——要更新 {site_file(s)} 里的 prompt_box")
            self.d.sleep(self.poll)

    def attach_count(self) -> int:
        return max(self.d.count(self.spec.attachment), self.d.composer_images(self.spec.prompt_box))

    def attached(self, before: int, n: int, secs: float) -> bool:
        s = self.spec
        start = self.clock()
        while True:
            got = self.attach_count() - before
            busy = bool(s.attachment_busy) and self.d.visible(s.attachment_busy)
            if got >= n and not busy:
                return True
            waited = self.clock() - start
            if not busy and got <= 0 and waited > secs:
                return False
            if waited > s.upload_timeout:
                if got > 0:
                    self.log(f"{self.label}：只看到 {got}/{n} 张图的缩略图，按已传上的继续")
                    return True
                raise BrowserError("timeout", f"{self.label} 图片 {s.upload_timeout:.0f} 秒还没传完，已放弃")
            self.d.sleep(self.poll)

    def upload(self, images: Sequence[Path]) -> None:
        """传图：上传框 → 点开「+」菜单再找上传框 → 往输入框里粘贴，每种都确认输入框里真出现了缩略图。"""
        s = self.spec
        if not images:
            return
        files = [ref_payload(Path(p)) for p in images]
        n = len(files)
        self.log(f"{self.label}：上传 {n} 张图")
        before = self.attach_count()
        tried: list[str] = []
        ok = self.d.set_files(s.file_input, files, s.prompt_box)
        if not ok and len(s.upload_buttons) > 1:
            # 最后一个按钮会弹文件选择框（扩展替不了用户选文件），不点
            for sel in s.upload_buttons[:-1]:
                if self.d.click(sel):
                    self.d.sleep(0.6)
            ok = self.d.set_files(s.file_input, files, s.prompt_box)
        if ok:
            tried.append("上传框")
            if self.attached(before, n, 15):
                self.log(f"{self.label}：图片已传上（上传框）")
                return
        if self.d.paste_files(s.prompt_box, files):
            tried.append("粘贴")
            if self.attached(before, n, 8):
                self.log(f"{self.label}：图片已传上（粘贴）")
                return
        raise BrowserError("page", f"{self.label} 图片没传上去（试过：{'、'.join(tried) or '没找到可用的方式'}），这次没有发送。"
                                   f"网站可能改版了，要更新 {site_file(s)} 里的 file_input / attachment")

    def submit(self, prompt: str) -> None:
        s = self.spec
        if not self.d.type_text(s.prompt_box, prompt):
            raise BrowserError("page", f"{self.label} 没能把提示词输进输入框。网站改版了，要更新 {site_file(s)} 里的 prompt_box")
        start = self.clock()
        while not self.d.enabled(s.send_button):   # 图片还在处理时发送按钮是灰的
            if self.clock() - start > SEND_WAIT:
                break
            self.d.sleep(self.poll)
        if not self.d.click(s.send_button) and not self.d.press(s.prompt_box, "Enter"):
            raise BrowserError("page", f"{self.label} 没能点发送。网站改版了，要更新 {site_file(s)} 里的 send_button")

    def busy(self) -> bool:
        s = self.spec
        if self.d.visible(s.stop_button):
            return True
        return bool(s.busy_marker) and self.d.visible(s.busy_marker)

    def wait_reply(self, before: int, timeout: float, before_text: str = "") -> str:
        """等回复写完：不在生成，并且回复文字连续 settle 秒不变。返回回复全文。"""
        s = self.spec
        start = self.clock()
        started = False
        last: str | None = None
        stable_since = 0.0
        next_report = self.report_every
        text = ""
        while True:
            now = self.clock()
            elapsed = now - start
            if elapsed > timeout:
                if self.d.visible(s.stop_button):
                    self.d.click(s.stop_button)
                raise BrowserError("timeout", f"{self.label} 超过 {timeout:.0f} 秒还没写完（已写 {len(text)} 字），已放弃。"
                                              "输出太长的步骤（剧本、分镜）换 API 或本机 Claude")
            busy = self.busy()
            text, n = self.d.reply_text(self.reply_sel, self.reply_ex)
            if n <= before and text == before_text:   # 还是发送前就有的那条（或没有回复）
                text = ""
            started = started or busy or bool(text.strip())
            alert = self.d.text(s.alert) if s.alert else ""
            if _match(s.quota_patterns, alert):
                raise BrowserError("quota", f"{self.label} 用量到上限或被限流：{excerpt(alert)}。等一阵再试，或换别的文本模型")
            if not started:
                self.check_login()
                if elapsed > self.start_timeout:
                    raise BrowserError("page", f"{self.label} 发出去 {self.start_timeout:.0f} 秒还没开始回复（没看到在生成，也没有回复）。"
                                               f"可能没发出去，或网站改版了——要更新 {site_file(s)} 里的 send_button / text_reply")
            if text.strip() and not busy:
                if text != last:
                    last, stable_since = text, now
                elif now - stable_since >= self.settle:
                    return text
            else:
                last = None
            if elapsed >= next_report:
                next_report += self.report_every
                state = "还在生成" if busy else ("在等文字稳定" if text else "还没开始回复")
                self.log(f"{self.label}：已等 {elapsed:.0f} 秒，回复 {len(text)} 字，{state}")
            self.d.sleep(self.poll)


def ask(d: ChatDriver, spec: SiteSpec, prompt: str, images: Sequence[Path] = (), *, timeout: float,
        log: Log, fresh: bool = False, clock: Callable[[], float] = time.monotonic, poll: float = 1.0,
        settle: float = SETTLE_SECONDS, start_timeout: float = START_TIMEOUT,
        report_every: float = REPORT_EVERY) -> str:
    """开新对话问一次，返回最后一条回复的全文。fresh：标签页刚新开（已经在新对话上），不用再跳转。"""
    f = ChatFlow(d, spec, log=log, clock=clock, poll=poll, settle=settle, start_timeout=start_timeout,
                 report_every=report_every)
    log(f"{f.label}：打开新对话")
    f.open_chat(fresh)
    before_text, before = d.reply_text(f.reply_sel, f.reply_ex)
    f.upload(list(images or ()))
    f.submit(prompt)
    t0 = clock()
    log(f"{f.label}：已发送（{len(prompt)} 字），等回复写完（最长 {timeout:.0f} 秒）")
    text = f.wait_reply(before, timeout, before_text)
    log(f"{f.label}：回复写完，{len(text)} 字，用时 {clock() - t0:.0f} 秒")
    return text
