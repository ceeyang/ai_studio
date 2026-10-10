"""一个网站的出图配置：页面地址、登录 cookie、选择器、文案匹配。flow.py 只认这里的字段。

选择器是 Playwright 的 CSS 选择器（可以用 :has-text() 等扩展），同一项里用逗号写多个备选，
任一个命中就算。文案匹配用正则，英文和中文界面都要覆盖。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


@dataclass(frozen=True)
class SiteSpec:
    key: str                  # chatgpt / gemini
    label: str                # 给人看的名字：ChatGPT / Gemini
    home: str                 # 登录用的首页
    new_chat_url: str         # 打开就是一个新对话
    host: str                 # 找已有标签页用
    # 登录 cookie：cookie_url 下有其中任一个就算登录了（doctor 用它做便宜的检查，不开页面）
    cookie_url: str
    session_cookies: tuple[str, ...]

    prompt_box: str           # 输入框（contenteditable）
    send_button: str          # 发送按钮
    stop_button: str          # 生成中才有的「停止」按钮
    login_wall: str           # 没登录时才有的元素（登录按钮等）
    file_input: str           # 隐藏的 <input type=file>，能直接 set_input_files
    upload_buttons: tuple[str, ...]   # 没有 file input 时依次点这些按钮，最后一个弹文件选择框
    attachment: str           # 输入框里已上传参考图的缩略图
    attachment_busy: str      # 参考图上传中的转圈
    assistant_turn: str       # 网站的回复（取最后一个）
    result_images: str        # 回复里生成的图片
    user_images: str          # 用户自己那条消息里的图（参考图），从结果里排除
    busy_marker: str = ""     # 看得见就说明这一轮还在生成（停止按钮之外的第二个判断，可空）
    download_buttons: tuple[str, ...] = ()   # 图上的「下载原图」按钮（先悬停图片再点）
    prefer_download_button: bool = False     # True：先点下载按钮，失败再直接取 src
    dismiss_buttons: str = ""                # 偶尔弹出的同意/知道了对话框按钮，看见就点掉
    alert: str = '[role="alert"]'            # 页面上的提示条（限额、出错常在这里，不在回复里）
    quota_patterns: tuple[re.Pattern[str], ...] = ()
    refusal_patterns: tuple[re.Pattern[str], ...] = ()
    image_src_hint: tuple[str, ...] = ()     # 生成图 src 里该有的片段（任一），用来排除头像/图标
    min_image_px: int = 384                  # 宽高较大的一边小于这个的图不算结果（头像、图标、缩略图）
    ready_timeout: float = 45.0              # 打开新对话后等输入框出现
    upload_timeout: float = 90.0             # 等参考图上传完
    settle_seconds: float = 4.0              # 停止按钮消失后图片 src 保持不变多久算出完
    no_image_grace: float = 12.0             # 停止按钮消失、有文字没图片，再等这么久还没图就判没出图
    # 带参考图时，提交后这么多秒内就出现的「结果图」当成网站回显的参考图（user_images 选择器失效时兜底）
    echo_seconds: float = 5.0
    expected_seconds: float = 90.0           # 进度条按这个时长估
    extra: dict = field(default_factory=dict)
