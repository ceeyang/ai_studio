"""ChatGPT 网页版（chatgpt.com）出图。

出图走对话里的图片生成工具：提示词开头写明「直接生成一张图」，它就会调图片生成，一张通常 1–3 分钟，
出图过程中会先显示模糊的半成品，停止按钮消失、图片地址不再变化才算出完。

选择器改版时最容易坏的几项（按顺序排查）：
1. prompt_box：输入框是 ProseMirror 的 contenteditable，id 一直是 prompt-textarea。
2. send_button / stop_button：靠 data-testid，偶尔会改名。
3. result_images：生成的图在助手那条消息里，地址是 chatgpt.com/backend-api/estuary/content?id=file_…
   （image_src_hint 用它排除头像、图标）；用户上传的参考图也是这个地址，靠 user_images 排除。
4. login_wall：没登录时右上角有 data-testid=login-button 的「登录」按钮；更可靠的是 cookie
   __Secure-next-auth.session-token（太长时拆成 .0/.1 两段）。

2026-10 实测（未登录页）：chatgpt.com 换成了新界面——输入框是 textarea#mobile-composer-prompt
（占位「询问 ChatGPT」），发送按钮 aria-label「发送消息」，「添加文件等」按钮下有「照片」，隐藏的
input#octane-…-photos-input（accept=image/*，multiple）；未登录时到处是文字为「登录」的按钮，
「创建图像」按钮写着「登录后使用」。下面的选择器新旧两套界面都写上了；登录后的回复区（助手消息、
生成图）还没在新界面上实测过，坏了先看 assistant_turn / result_images / stop_button。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec, rx

CHATGPT = SiteSpec(
    key="chatgpt",
    label="ChatGPT",
    home="https://chatgpt.com/",
    new_chat_url="https://chatgpt.com/",
    host="chatgpt.com",
    cookie_url="https://chatgpt.com/",
    session_cookies=("__Secure-next-auth.session-token", "__Secure-next-auth.session-token.0"),

    prompt_box=('#prompt-textarea, div.ProseMirror[contenteditable="true"], '
                'textarea[id$="composer-prompt"], textarea[placeholder*="ChatGPT"]'),
    send_button=('button[data-testid="send-button"], #composer-submit-button, '
                 'button[aria-label="发送消息"], button[aria-label="Send message"], '
                 'button[aria-label="Send prompt"], button[aria-label="发送提示"]'),
    stop_button=('button[data-testid="stop-button"], button[aria-label="Stop streaming"], '
                 'button[aria-label="停止流式传输"], button[aria-label="停止"], button[aria-label="Stop"], '
                 'button[aria-label*="停止生成"], button[aria-label*="Stop generating"]'),
    login_wall=('[data-testid="login-button"], [data-testid="signup-button"], '
                'button:text-is("登录"), button:text-is("Log in")'),
    # 输入框旁边有几个隐藏的 file input（照片/文件/相机），都收图片；取第一个收图片的
    file_input='input[type="file"][accept*="image"]',
    upload_buttons=(
        'button[data-testid="composer-plus-btn"], button[aria-label="Add photos and files"], '
        'button[aria-label="添加照片和文件"], button[aria-label="添加文件等"], '
        'button[aria-label="Add files and more"]',
        '[role="menuitem"]:has-text("Add photos"), [role="menuitem"]:has-text("添加照片"), '
        'button:text-is("照片"), button:text-is("Photos")',
    ),
    attachment='form img',
    attachment_busy='form [role="progressbar"], form .animate-spin',
    # 新界面（2026-10）没有 article / data-message-author-role 了：取最后一轮（data-talvt-turn-state），去掉用户那条消息
    assistant_turn=('article[data-turn="assistant"], [data-message-author-role="assistant"], '
                    '[data-talvt-turn-state]'),
    reply_exclude='[data-user-message-bubble]',
    result_images=(  # 2026-10 实测的新界面：结果图在 generated-image-gallery 里，alt 是「Generated image 1」，地址是 blob:
                   '[data-testid="generated-image-gallery"] img, img[alt^="Generated image"], '
                   'article[data-turn="assistant"] img, [data-message-author-role="assistant"] img, '
                   'img[alt*="已生成图片"], img[alt*="生成的图片"], '
                   # 兜底取页面里所有 estuary 地址的图（参考图靠 user_images 和回显规则排除）
                   'main img[src*="/backend-api/estuary/content"]'),
    user_images=('[data-user-message-bubble] img, article[data-turn="user"] img, '
                 '[data-message-author-role="user"] img, form img'),
    # 新界面里这一轮还没标成 complete 也算还在出图（停止按钮不一定看得见）
    busy_marker='[data-talvt-turn-state]:not([data-talvt-turn-state="complete"])',
    # 悬停在图上会出现「下载」按钮；默认直接取 src（同源、带 cookie），按钮只是备用
    download_buttons=('button[aria-label="Download this image"], button[aria-label="下载此图片"], '
                      'button[aria-label="下载图片"], button[aria-label="Download"]',),
    prefer_download_button=False,
    dismiss_buttons=('[role="dialog"] button:has-text("Got it"), [role="dialog"] button:has-text("知道了"), '
                     '[role="dialog"] button:has-text("Okay, let’s go")'),
    quota_patterns=rx(
        r"hit (the|your) .{0,40}limit", r"reached (the|your|our) .{0,40}limit",
        r"limit (for|on) image", r"rate limit", r"too many requests",
        r"try again (later|in \d|after)", r"达到.{0,12}(上限|限制)", r"次数.{0,6}(用完|上限)",
        r"请求(过多|太多)", r"稍后再试"),
    refusal_patterns=rx(
        r"can[’']?t (help|create|generate|make|produce)", r"(unable|not able) to (create|generate|help|make)",
        r"content polic", r"violat", r"won[’']?t be able", r"无法(生成|创建|制作|帮|提供|满足)",
        r"不能(生成|创建|制作|帮)", r"违反", r"内容政策"),
    image_src_hint=("/backend-api/estuary/content", "oaiusercontent.com", "/backend-api/files",
                    "blob:https://chatgpt.com/"),
    min_image_px=384,
    settle_seconds=5.0,
    no_image_grace=15.0,
    expected_seconds=120.0,
)
