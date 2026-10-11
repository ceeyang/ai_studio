"""Gemini 网页版（gemini.google.com/app）出图。

提示词开头写明「生成一张图」，Gemini 会自动用它的图像模型出图，通常几十秒。页面是 Angular，元素多是
自定义标签（rich-textarea、model-response、user-query、generated-image），比 class 名稳定。

选择器改版时最容易坏的几项：
1. prompt_box：rich-textarea 里的 Quill 编辑器 div.ql-editor（contenteditable）。
2. upload_buttons：没有常驻的 file input，要先点「+」打开上传菜单，再点「上传文件」弹文件选择框；
   都不行时 flow 会退回往输入框里粘贴图片。第一次上传可能弹同意条款，dismiss_buttons 负责点掉。
3. result_images：回复里 generated-image / single-image 下的 img，地址在 googleusercontent.com。
   页面里显示的是缩小图，原图用图上的「下载原尺寸图片」按钮拿（prefer_download_button）。
4. 登录：Google 账号 cookie（__Secure-1PSID 等，域 .google.com）；没登录时页面右上角是「登录」链接。
"""
from __future__ import annotations

from backend.engines.browser.sites.base import SiteSpec, rx

GEMINI = SiteSpec(
    key="gemini",
    label="Gemini",
    home="https://gemini.google.com/app",
    new_chat_url="https://gemini.google.com/app",
    host="gemini.google.com",
    cookie_url="https://gemini.google.com/",
    session_cookies=("__Secure-1PSID", "__Secure-3PSID"),

    prompt_box=('rich-textarea div.ql-editor[contenteditable="true"], div.ql-editor[contenteditable="true"], '
                'div[role="textbox"][contenteditable="true"]'),
    send_button=('button.send-button, button[aria-label="Send message"], button[aria-label="发送"], '
                 'button[aria-label="发送消息"]'),
    stop_button=('button[aria-label="Stop response"], button[aria-label="停止回答"], '
                 'button[aria-label="停止生成"], button.send-button.stop'),
    login_wall=('a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/v3/signin"], '
                'a[aria-label="Sign in"], a[aria-label="登录"]'),
    file_input='input[type="file"]',
    upload_buttons=(
        'button[aria-label="Open upload file menu"], button[aria-label="打开文件上传菜单"], '
        'button.upload-card-button',
        'button[data-test-id="local-images-files-uploader-button"], button[aria-label*="Upload files"], '
        'button[aria-label*="上传文件"], [role="menuitem"]:has-text("Upload files"), '
        '[role="menuitem"]:has-text("上传文件")',
    ),
    attachment='uploader-file-preview, img[data-test-id="image-preview"], .file-preview-container img',
    attachment_busy='uploader-file-preview mat-progress-spinner, uploader-file-preview .loading',
    assistant_turn='model-response',
    result_images=('model-response generated-image img, model-response single-image img, '
                   'model-response img.image'),
    user_images='user-query img, uploader-file-preview img',
    # 网页文本模型读回复：回复正文在 message-content 里
    text_reply='model-response message-content, model-response .model-response-text, model-response',
    text_exclude='model-thoughts, .thoughts-container, button, .code-block-decoration, .sr-only',
    download_buttons=('button[aria-label="Download full size image"], button[aria-label="下载原尺寸图片"], '
                      'button[aria-label="下载完整尺寸的图片"], button[data-test-id="download-generated-image-button"], '
                      'download-generated-image-button button',),
    prefer_download_button=True,
    dismiss_buttons=('mat-dialog-container button:has-text("Agree"), mat-dialog-container button:has-text("同意"), '
                     'mat-dialog-container button:has-text("Got it"), mat-dialog-container button:has-text("知道了")'),
    alert='mat-snack-bar-container, snack-bar-container, [role="alert"]',
    quota_patterns=rx(
        r"reached (the|your) .{0,40}limit", r"limit (for|on) (creating )?image", r"try again later",
        r"come back (later|tomorrow)", r"too many requests", r"达到.{0,12}(上限|限制)", r"稍后再试",
        r"明天再"),
    refusal_patterns=rx(
        r"can[’']?t (help|create|generate|make)", r"(unable|not able) to (create|generate|help|make)",
        r"just a language model", r"我无法", r"无法(生成|创建|制作|帮)", r"只是一个语言模型",
        r"不能(生成|创建|制作|帮)"),
    image_src_hint=("googleusercontent.com", "blob:"),
    min_image_px=384,
    settle_seconds=4.0,
    no_image_grace=12.0,
    conversation_re=r"/app/([0-9A-Za-z]{8,})",
    expected_seconds=60.0,
    # 出视频（Veo）：输入框左边「Upload & tools」→「Create video」，第一次弹介绍框点「Try it」；
    # 画幅在输入框下面的「Aspect ratio」菜单里选（只有 16:9 / 9:16）；时长不能选，总是约 10 秒。
    # 回复先说「I'm generating your video…」，几分钟后出 generated-video，地址在 usercontent.google.com。
    result_videos="model-response generated-video video",
    video_src_hint=("usercontent.google.com", "googleusercontent.com", "blob:"),
    video_steps=(
        'button[aria-label="Upload & tools"], button[aria-label="上传和工具"], button[aria-label="上传文件和工具"]',
        '.cdk-overlay-container button:has-text("Create video"), .cdk-overlay-container button:has-text("制作视频"), '
        '.cdk-overlay-container button:has-text("创建视频"), .cdk-overlay-container button:has-text("生成视频")',
    ),
    video_active='button[aria-label="Deselect Videos"], button[aria-label*="取消选择视频"]',
    video_dismiss='.cdk-overlay-container button:has-text("Try it"), .cdk-overlay-container button:has-text("试试")',
    video_aspect_button='button[aria-label^="Aspect ratio"], button[aria-label^="宽高比"]',
    video_aspect_options={
        "9:16": '[role="menuitemradio"]:has-text("9:16")',
        "16:9": '[role="menuitemradio"]:has-text("16:9")',
    },
    video_pending_patterns=rx(r"generating your video", r"could take a few minutes", r"正在生成", r"生成.{0,6}视频"),
    video_expected_seconds=180.0,
)
