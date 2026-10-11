"""Claude 网页版（claude.ai）——只做文本（网页文本模型 web_claude），不出图。

注意：下面的选择器【未经实测】，按 2026 年 claude.ai 界面的公开结构写的，每项都多写了几个兜底。
文本流程（backend/engines/browser/chat.py）每一步都把这里的选择器发给扩展，所以改这里、重启 studio 就生效，
不用重新加载扩展；browser-extension/sites/claude.js 是同一份（给扩展报登录状态用，比对测试保证两边一致）。

1. prompt_box：输入框是 ProseMirror 的 contenteditable（div.ProseMirror），外面包着 data-testid=chat-input。
2. send_button / stop_button：aria-label「Send message」/「Stop response」（中文界面「发送消息」/「停止响应」）。
3. 回复：助手消息在 .font-claude-response（老界面 .font-claude-message）里，生成中外层带 data-is-streaming="true"。
4. 登录：没登录会跳到 claude.ai/login（页面上有「Continue with Google」按钮和邮箱输入框）；登录 cookie 是 sessionKey。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec, rx

CLAUDE = SiteSpec(
    key="claude",
    label="Claude",
    home="https://claude.ai/",
    new_chat_url="https://claude.ai/new",
    host="claude.ai",
    cookie_url="https://claude.ai/",
    session_cookies=("sessionKey",),

    prompt_box=('div.ProseMirror[contenteditable="true"], [data-testid="chat-input"] [contenteditable="true"], '
                'div[contenteditable="true"][aria-label*="Claude"], fieldset div[contenteditable="true"]'),
    send_button=('button[aria-label="Send message"], button[aria-label="Send Message"], button[aria-label="发送消息"], '
                 'button[aria-label="发送"], button[data-testid="send-button"]'),
    stop_button=('button[aria-label="Stop response"], button[aria-label="停止响应"], button[aria-label="停止回复"], '
                 'button[aria-label="停止生成"], button[data-testid="stop-button"]'),
    login_wall=('a[href="/login"], button:has-text("Continue with Google"), button:has-text("Continue with email"), '
                'button:has-text("使用 Google 继续"), input#email[type="email"]'),
    file_input='input[type="file"][data-testid="file-upload"], input[type="file"]',
    upload_buttons=(
        'button[data-testid="input-menu-plus"], button[aria-label="Add files, connectors, and more"], '
        'button[aria-label*="Attach"], button[aria-label*="添加文件"]',
        '[role="menuitem"]:has-text("Upload a file"), [role="menuitem"]:has-text("上传文件")',
    ),
    attachment='[data-testid="file-thumbnail"], [data-testid*="attachment"] img, fieldset img',
    attachment_busy='[data-testid="file-thumbnail"] [role="progressbar"], fieldset .animate-spin',
    assistant_turn='.font-claude-response, .font-claude-message, [data-is-streaming]',
    result_images="",          # 只做文本，不出图
    user_images='[data-testid="user-message"] img',
    busy_marker='[data-is-streaming="true"]',
    text_reply=('.font-claude-response, .font-claude-message, [data-is-streaming] .standard-markdown, '
                '[data-is-streaming]'),
    text_exclude='button, [data-testid="action-bar-copy"], .sr-only',
    dismiss_buttons='[role="dialog"] button:has-text("Got it"), [role="dialog"] button:has-text("知道了")',
    quota_patterns=rx(
        r"out of (free )?messages", r"usage limit", r"message limit", r"reached .{0,40}limit",
        r"too many requests", r"达到.{0,12}(上限|限制)", r"稍后再试"),
    refusal_patterns=rx(r"can[’']?t help with", r"(unable|not able) to help", r"无法(帮|提供|满足)"),
    image_src_hint=(),
    min_image_px=384,
    settle_seconds=4.0,
    no_image_grace=12.0,
    expected_seconds=60.0,
)
