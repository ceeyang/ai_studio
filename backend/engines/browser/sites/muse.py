"""Meta Muse 网页版（muse.ai）出图、出视频。和 browser-extension/sites/muse.js 是同一份选择器——
网站改版时两边一起改（backend/tests/test_browser_extension_js.py 会比对两边是否一致）。

Muse 是常驻智能体：一个主对话（用户自己的事都在里面）+ 若干 side chat。studio 只用 side chat：
https://muse.ai/thread/new 开一个新的，发出第一条后地址变成 https://muse.ai/thread/<uuid>，之后可以直接打开
接着发（会话复用见 backend/engines/browser/conversations.py）。提示词里说「生成一张图 / 一段视频」就会出，
画幅和时长都写进提示词：出图约 25 秒，出视频约 1 分钟（有声，24 fps；时长只认 3 / 5 / 10 秒几档，不一定照做）。

选择器改版时最容易坏的几项：
1. prompt_box：textarea[aria-label="Message"]；发送按钮 aria-label="Send"（有字或有附件才出现，回车也能发）。
2. 上传：输入框旁边有隐藏的 input[type=file]（multiple），直接塞文件就行；缩略图上有「Remove attachment」按钮。
3. 结果：消息带 data-message-role（assistant / user）；生成的图在
   data-testid="hatch-chat-attachment-presentation-image" 里，视频在 …-presentation-video 里，地址都是
   blob:https://muse.ai/…（页面里 fetch 拿原文件）。页面右边常驻一个 480×480 的头像动画 <video>，
   不在消息里，所以视频选择器要限定在 assistant 消息里。
4. 登录：Meta 账号（Facebook 登录），cookie hatch_sess（域 .muse.ai）；没登录时 muse.ai 跳 auth.muse.ai → facebook.com。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec, rx

MUSE = SiteSpec(
    key="muse",
    label="Muse",
    home="https://muse.ai/",
    new_chat_url="https://muse.ai/thread/new",
    host="muse.ai",
    cookie_url="https://muse.ai/",
    session_cookies=("hatch_sess",),

    prompt_box='textarea[aria-label="Message"], textarea[placeholder="Message"]',
    send_button='button[aria-label="Send"], button[aria-label="发送"]',
    stop_button='button[aria-label="Stop"], button[aria-label="停止"]',
    login_wall=('a[href*="auth.muse.ai"], a[href*="facebook.com/login"], button:has-text("Continue with Facebook"), '
                'button:has-text("Log in")'),
    file_input='input[type="file"]',
    # 回形针按钮会弹文件选择框（扩展替不了用户选文件）：只在找不到 file input 时兜底，flow 不点最后一个
    upload_buttons=('button[aria-label="Attach file"], button[aria-label="添加附件"]',),
    attachment='button[aria-label="Remove attachment"], button[aria-label="移除附件"]',
    attachment_busy="",
    assistant_turn='[data-message-role="assistant"]',
    result_images='[data-message-role="assistant"] [data-testid="hatch-chat-attachment-presentation-image"] img',
    user_images='[data-message-role="user"] img',
    text_reply='[data-message-role="assistant"]',
    alert='[role="alert"], [data-sonner-toast]',
    quota_patterns=rx(
        r"reached (the|your) .{0,40}limit", r"limit (for|on) (creating|generating)", r"try again later",
        r"come back (later|tomorrow)", r"too many requests", r"达到.{0,12}(上限|限制)", r"稍后再试", r"明天再"),
    refusal_patterns=rx(
        r"can[’']?t (help|create|generate|make)", r"(unable|not able) to (create|generate|help|make)",
        r"against (our|my) (policies|guidelines)", r"我无法", r"无法(生成|创建|制作|帮)", r"不能(生成|创建|制作|帮)"),
    image_src_hint=("blob:https://muse.ai/", "muse.ai"),
    min_image_px=384,
    settle_seconds=3.0,
    no_image_grace=15.0,
    expected_seconds=30.0,
    result_videos='[data-message-role="assistant"] [data-testid="hatch-chat-attachment-presentation-video"] video',
    video_src_hint=("blob:https://muse.ai/", "muse.ai"),
    video_pending_patterns=rx(r"working on (it|your video)", r"generating", r"正在生成"),
    video_expected_seconds=70.0,
    # 续接网页会话：side chat 的地址 /thread/<uuid>（/thread/new 是新对话入口，不算）
    conversation_re=r"/thread/([0-9a-fA-F][0-9a-fA-F-]{7,})",
)
