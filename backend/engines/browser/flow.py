"""通用网页出图流程：新对话（或续接之前的对话）→ 传参考图 → 输提示词 → 提交 → 等图出完 → 下原图。

续接之前的对话（conversation_url）时：先确认那条对话打得开（地址还是那条、输入框出来了、看得到之前的
回复），打不开就新开；提交前记下页面上已有的图片地址和最后一条回复，等图时只认这之后新出的图，
长会话里不会把上一张图当成这次的结果。

只通过 PageDriver 操作页面，网站差异全在 SiteSpec（sites/）。任何一步不对都抛 BrowserError，
文案说清楚是没登录、到限额、被拒、超时还是网站改版，不静默失败。
"""
from __future__ import annotations

import io
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from backend.engines.browser.driver import ImageInfo, PageDriver
from backend.engines.browser.errors import LOGIN_HINT, BrowserError, excerpt
from backend.engines.browser.sites.base import SiteSpec
from backend.queue.errors import JobCanceled

Log = Callable[[str], None]
Progress = Callable[[int, int], None]
PROGRESS_MAX = 100
CONTINUE_CHECK = 15.0     # 打开之前的对话后最多等这么久看到之前的回复，看不到就当这条对话没了
SNAPSHOT_WAIT = 10.0      # 续接时等页面上已有的图加载稳定（再记「之前的图」）最多这么久


@dataclass
class GeneratedImage:
    data: bytes
    src: str
    via: str            # fetch / download
    width: int
    height: int
    format: str         # PNG / JPEG / WEBP


def not_logged_in(spec: SiteSpec) -> BrowserError:
    return BrowserError("not_logged_in",
                        f"{spec.label} 没有登录：{LOGIN_HINT}，在弹出的浏览器窗口里登录 {spec.host}"
                        f"（只需一次），再重试这个任务")


def logged_in_by_cookie(driver: PageDriver, spec: SiteSpec) -> bool:
    names = driver.cookie_names(spec.cookie_url)
    return any(c in names for c in spec.session_cookies)


def _match(patterns, text: str) -> bool:
    return bool(text) and any(p.search(text) for p in patterns)


class _Flow:
    def __init__(self, driver: PageDriver, spec: SiteSpec, *, cancel: threading.Event | None,
                 log: Log, progress: Progress, clock: Callable[[], float], poll: float):
        self.d, self.spec = driver, spec
        self.cancel = cancel or threading.Event()
        self.log, self.progress, self.clock, self.poll = log, progress, clock, poll
        self.echo: set[str] = set()     # 网站回显的参考图（提交后马上出现的图）
        self.n_refs = 0
        self.continued = False          # 在之前的对话里接着发
        self.before: set[str] = set()   # 提交前页面上就有的图（之前几轮的结果、参考图）
        self.before_reply = ""          # 提交前最后一条回复的文字

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            if self.d.visible(self.spec.stop_button):
                self.d.click(self.spec.stop_button)
            raise JobCanceled()

    def dismiss(self) -> None:
        if self.spec.dismiss_buttons and self.d.visible(self.spec.dismiss_buttons):
            self.d.click(self.spec.dismiss_buttons)

    # ------------------------------------------------------------ 步骤
    def open_chat(self) -> None:
        s = self.spec
        self.d.goto(s.new_chat_url)
        start = self.clock()
        while True:
            self.check_cancel()
            if self.d.visible(s.login_wall) or not logged_in_by_cookie(self.d, s):
                # cookie 刚写进去时可能慢一拍：输入框都出来了还没 cookie，再等一轮
                if self.d.visible(s.login_wall) or self.clock() - start > 5:
                    raise not_logged_in(s)
            elif self.d.visible(s.prompt_box):
                self.dismiss()
                return
            if self.clock() - start > s.ready_timeout:
                raise BrowserError("page", f"{s.label} 页面 {s.ready_timeout:.0f} 秒内没出现输入框"
                                   f"（当前地址 {self.d.url()}）。网络不通，或网站改版了——"
                                   f"要更新 backend/engines/browser/sites/{s.key}.py 里的 prompt_box")
            self.d.sleep(self.poll)

    def open_existing(self, url: str) -> str | None:
        """打开之前的对话：打得开返回 None，打不开返回中文原因（调用方新开）。没登录照样抛错。"""
        from backend.engines.browser.conversations import conversation_id

        s = self.spec
        want = conversation_id(s, url)
        self.d.goto(url)
        start = self.clock()
        while True:
            self.check_cancel()
            if self.d.visible(s.login_wall):
                raise not_logged_in(s)
            here = self.d.url()
            if want and conversation_id(s, here) != want:
                return f"会话打不开（跳到了 {here or '空白页'}，可能已被删除）"
            if self.d.visible(s.prompt_box):
                break
            if self.clock() - start > s.ready_timeout:
                return f"会话页面 {s.ready_timeout:.0f} 秒内没出现输入框（当前地址 {here}）"
            self.d.sleep(self.poll)
        start = self.clock()
        while self.d.count(s.assistant_turn) <= 0:   # 之前的回复要在页面上
            self.check_cancel()
            if self.clock() - start > CONTINUE_CHECK:
                return "会话里看不到之前的回复（可能已被删除或清空）"
            self.d.sleep(self.poll)
        self.dismiss()
        self.continued = True
        return None

    def snapshot(self) -> None:
        """提交前记下页面上已有的图和最后一条回复；续接时先等已有的图加载稳定。"""
        s = self.spec
        start = self.clock()
        last: tuple[str, ...] | None = None
        while True:
            srcs = tuple(sorted({i.src for i in self.d.images(s.result_images) if i.src}
                                | {i.src for i in self.d.images(s.user_images) if i.src}))
            if not self.continued or srcs == last or self.clock() - start > SNAPSHOT_WAIT:
                break
            last = srcs
            self.d.sleep(self.poll)
        self.before = set(srcs)
        self.before_reply = self.d.text(s.assistant_turn, s.reply_exclude) if self.continued else ""

    def upload(self, refs: list[Path]) -> None:
        s = self.spec
        if not refs:
            return
        self.n_refs = len(refs)
        self.log(f"{s.label}：上传 {len(refs)} 张参考图")
        before = self.d.count(s.attachment)
        ok = (self.d.set_files(s.file_input, refs)
              or self.d.choose_files(s.upload_buttons, refs)
              or self.d.paste_files(s.prompt_box, refs))
        if not ok:
            raise BrowserError("page", f"{s.label} 上传参考图失败：没找到上传入口。网站改版了，要更新 "
                               f"backend/engines/browser/sites/{s.key}.py 里的 file_input / upload_buttons")
        start = self.clock()
        while True:
            self.check_cancel()
            self.dismiss()
            n = self.d.count(s.attachment) - before
            busy = self.d.visible(s.attachment_busy)
            if n >= len(refs) and not busy:
                return
            waited = self.clock() - start
            if not busy and waited > 15:
                self.log(f"{s.label}：输入框里只看到 {max(n, 0)}/{len(refs)} 张参考图缩略图"
                         "（也可能是缩略图选择器过时），继续提交")
                return
            if waited > s.upload_timeout:
                raise BrowserError("timeout", f"{s.label} 参考图 {s.upload_timeout:.0f} 秒还没传完，已放弃")
            self.d.sleep(self.poll)

    def submit(self, prompt: str) -> None:
        s = self.spec
        if not self.d.type_text(s.prompt_box, prompt):
            raise BrowserError("page", f"{s.label} 没能把提示词输进输入框。网站改版了，要更新 "
                               f"backend/engines/browser/sites/{s.key}.py 里的 prompt_box")
        start = self.clock()
        while not self.d.enabled(s.send_button):   # 参考图还在处理时发送按钮是灰的
            self.check_cancel()
            if self.clock() - start > 30:
                break
            self.d.sleep(self.poll)
        if not self.d.click(s.send_button) and not self.d.press(s.prompt_box, "Enter"):
            raise BrowserError("page", f"{s.label} 没能点发送。网站改版了，要更新 "
                               f"backend/engines/browser/sites/{s.key}.py 里的 send_button")

    def _results(self) -> list[ImageInfo]:
        s = self.spec
        mine = {i.src for i in self.d.images(s.user_images)}
        out: dict[str, ImageInfo] = {}
        seen: set[tuple[str, int, int]] = set()   # 同一张图网站会再放一个副本（地址不同）：alt+尺寸相同只留第一张
        for i in self.d.images(s.result_images):
            if not i.src or i.src in mine or i.src in self.before or i.src in self.echo or i.src in out \
                    or not i.complete:
                continue
            if i.alt and (i.alt, i.width, i.height) in seen:
                continue
            if max(i.width, i.height) < s.min_image_px:
                continue
            if s.image_src_hint and not any(h in i.src for h in s.image_src_hint):
                continue
            if i.alt:
                seen.add((i.alt, i.width, i.height))
            out[i.src] = i
        return list(out.values())

    def wait_images(self, timeout: float) -> list[ImageInfo]:
        s = self.spec
        start = self.clock()
        last: tuple[str, ...] | None = None
        stable_since = idle_since = 0.0
        while True:
            self.check_cancel()
            now = self.clock()
            elapsed = now - start
            if elapsed > timeout:
                if self.d.visible(s.stop_button):
                    self.d.click(s.stop_button)
                raise BrowserError("timeout", f"{s.label} 超过 {timeout:.0f} 秒还没出完图，已放弃"
                                   "（浏览器里那条对话还在，可以去看看卡在哪，再重试）")
            reply = self.d.text(s.assistant_turn, s.reply_exclude)
            if self.continued and reply == self.before_reply:   # 还是上一轮的回复，这次的还没出来
                reply = ""
            alert = self.d.text(s.alert) if s.alert else ""
            for t in (alert, reply):
                if _match(s.quota_patterns, t):
                    raise BrowserError("quota", f"{s.label} 出图次数到上限或被限流：{excerpt(t)}。"
                                       "等一阵再重试，或换别的图像引擎")
            if self.d.visible(s.login_wall):
                raise not_logged_in(s)
            busy = self.d.visible(s.stop_button) or bool(s.busy_marker and self.d.visible(s.busy_marker))
            imgs = self._results()
            if (self.n_refs or self.continued) and elapsed <= s.echo_seconds and imgs:
                # 生成的图不可能几秒内就出来：这时冒出来的是网站回显的参考图
                self.echo.update(i.src for i in imgs)
                imgs = []
            srcs = tuple(i.src for i in imgs)
            if srcs and not busy:
                if srcs != last:
                    last, stable_since = srcs, now
                elif now - stable_since >= s.settle_seconds:
                    return imgs
            else:
                last = None
            if not busy and not srcs and reply.strip():
                idle_since = idle_since or now
                if now - idle_since >= s.no_image_grace:
                    if _match(s.refusal_patterns, reply):
                        raise BrowserError("refused", f"{s.label} 拒绝了这次出图（内容审核）：{excerpt(reply)}"
                                           "。改一下提示词再试")
                    raise BrowserError("no_image", f"{s.label} 回复了文字但没有出图：{excerpt(reply)}")
            else:
                idle_since = 0.0
            self.progress(min(95, int(elapsed / max(s.expected_seconds, 1) * 95)), PROGRESS_MAX)
            self.d.sleep(self.poll)

    def download(self, imgs: list[ImageInfo]) -> list[GeneratedImage]:
        s = self.spec
        out: list[GeneratedImage] = []
        errors: list[str] = []
        for info in imgs:
            self.check_cancel()
            order = ("download", "fetch") if s.prefer_download_button else ("fetch", "download")
            for via in order:
                try:
                    if via == "download":
                        data = None
                        for sel in s.download_buttons:
                            data = self.d.download(info.src, sel)
                            if data:
                                break
                        if not data:
                            raise RuntimeError("没找到下载按钮或没接到下载")
                    else:
                        data = self.d.fetch(info.src)
                    im = Image.open(io.BytesIO(data))
                    im.load()
                except JobCanceled:
                    raise
                except Exception as e:
                    errors.append(f"{via}：{type(e).__name__} {e}")
                    continue
                out.append(GeneratedImage(data, info.src, via, im.width, im.height,
                                          (im.format or "PNG").upper()))
                break
        if not out:
            raise BrowserError("page", f"{s.label} 出了图但下载原图失败：{'；'.join(errors)[:300]}")
        return out


def generate(driver: PageDriver, spec: SiteSpec, prompt: str, refs: list[Path], *, timeout: float,
             cancel: threading.Event | None = None, log: Log | None = None,
             progress: Progress | None = None, clock: Callable[[], float] = time.monotonic,
             poll: float = 1.0, conversation_url: str | None = None,
             info: dict | None = None) -> list[GeneratedImage]:
    """conversation_url：在这条之前的对话里接着出图（打不开就新开）。
    info（可选）：填 {url: 结束时标签页地址, continued: 是否真的续接上了, note: 退回新对话的原因}。"""
    log = log or (lambda m: None)
    progress = progress or (lambda v, m: None)
    f = _Flow(driver, spec, cancel=cancel, log=log, progress=progress, clock=clock, poll=poll)
    progress(0, PROGRESS_MAX)
    note = ""
    if conversation_url:
        log(f"{spec.label}：打开之前的会话 {conversation_url}")
        why = f.open_existing(conversation_url)
        if why:
            note = why
            log(f"{spec.label}：{why}，新开对话")
    if not f.continued:
        log(f"{spec.label}：打开新对话")
        f.open_chat()
    f.snapshot()
    f.upload(list(refs))
    f.submit(prompt)
    log(f"{spec.label}：已提交，等出图（最长 {timeout:.0f} 秒）")
    t0 = clock()
    imgs = f.wait_images(timeout)
    log(f"{spec.label}：出了 {len(imgs)} 张图，用时 {clock() - t0:.0f} 秒，下载原图")
    progress(PROGRESS_MAX, PROGRESS_MAX)
    got = f.download(imgs)
    for g in got:
        log(f"{spec.label}：原图 {g.width}×{g.height} {g.format}（{'下载按钮' if g.via == 'download' else '直接取图片地址'}）")
    if info is not None:
        info.update(url=driver.url(), continued=f.continued, note=note)
    return got
