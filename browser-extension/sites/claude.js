/*
 * Claude 网页版（claude.ai）——只做文本（网页文本模型 web_claude），不出图。和
 * backend/engines/browser/sites/claude.py 是同一份选择器——网站改版时两边一起改
 * （backend/tests/test_browser_extension_js.py 会比对两边是否一致）。
 *
 * 注意：下面的选择器【未经实测】，按 2026 年 claude.ai 界面的公开结构写的，每项都多写了几个兜底。
 * 文本流程由 studio（backend/engines/browser/chat.py）通过扩展的 rpc 一步步驱动，选择器以 Python 那份为准，
 * 改 Python 那份、重启 studio 就生效，不用重新加载扩展；这里的一份给扩展报登录状态用。
 *
 * 1. prompt_box：输入框是 ProseMirror 的 contenteditable（div.ProseMirror），外面包着 data-testid=chat-input。
 * 2. send_button / stop_button：aria-label「Send message」/「Stop response」（中文界面「发送消息」/「停止响应」）。
 * 3. 回复：助手消息在 .font-claude-response（老界面 .font-claude-message）里，生成中外层带 data-is-streaming="true"。
 * 4. 登录：没登录会跳到 claude.ai/login（页面上有「Continue with Google」按钮和邮箱输入框）；登录 cookie 是 sessionKey。
 */
export const CLAUDE = {
  key: 'claude',
  label: 'Claude',
  home: 'https://claude.ai/',
  new_chat_url: 'https://claude.ai/new',
  host: 'claude.ai',
  hosts: ['claude.ai'],
  login_hosts: [], // 没登录时跳的是同域名下的 /login，靠 login_wall 认
  cookie_url: 'https://claude.ai/',
  session_cookies: ['sessionKey'],

  prompt_box:
    'div.ProseMirror[contenteditable="true"], [data-testid="chat-input"] [contenteditable="true"], ' +
    'div[contenteditable="true"][aria-label*="Claude"], fieldset div[contenteditable="true"]',
  send_button:
    'button[aria-label="Send message"], button[aria-label="Send Message"], button[aria-label="发送消息"], ' +
    'button[aria-label="发送"], button[data-testid="send-button"]',
  stop_button:
    'button[aria-label="Stop response"], button[aria-label="停止响应"], button[aria-label="停止回复"], ' +
    'button[aria-label="停止生成"], button[data-testid="stop-button"]',
  login_wall:
    'a[href="/login"], button:has-text("Continue with Google"), button:has-text("Continue with email"), ' +
    'button:has-text("使用 Google 继续"), input#email[type="email"]',
  file_input: 'input[type="file"][data-testid="file-upload"], input[type="file"]',
  upload_buttons: [
    'button[data-testid="input-menu-plus"], button[aria-label="Add files, connectors, and more"], ' +
      'button[aria-label*="Attach"], button[aria-label*="添加文件"]',
    '[role="menuitem"]:has-text("Upload a file"), [role="menuitem"]:has-text("上传文件")',
  ],
  attachment: '[data-testid="file-thumbnail"], [data-testid*="attachment"] img, fieldset img',
  attachment_busy: '[data-testid="file-thumbnail"] [role="progressbar"], fieldset .animate-spin',
  assistant_turn: '.font-claude-response, .font-claude-message, [data-is-streaming]',
  result_images: '', // 只做文本，不出图
  user_images: '[data-testid="user-message"] img',
  busy_marker: '[data-is-streaming="true"]',
  reply_exclude: '',
  text_reply:
    '.font-claude-response, .font-claude-message, [data-is-streaming] .standard-markdown, [data-is-streaming]',
  text_exclude: 'button, [data-testid="action-bar-copy"], .sr-only',
  dismiss_buttons: '[role="dialog"] button:has-text("Got it"), [role="dialog"] button:has-text("知道了")',
  alert: '[role="alert"]',
  quota_patterns: [
    /out of (free )?messages/i, /usage limit/i, /message limit/i, /reached .{0,40}limit/i,
    /too many requests/i, /达到.{0,12}(上限|限制)/i, /稍后再试/i,
  ],
  refusal_patterns: [/can[’']?t help with/i, /(unable|not able) to help/i, /无法(帮|提供|满足)/i],
  image_src_hint: [],
  min_image_px: 384,
  ready_timeout: 45,
  upload_timeout: 90,
  settle_seconds: 4,
  no_image_grace: 12,
  echo_seconds: 5,
  // 对话地址里的对话 id（第 1 组）：续接网页会话时认地址用，和 backend/engines/browser/sites/claude.py 一致
  conversation_re: '/chat/([0-9A-Za-z][0-9A-Za-z-]{7,})',
  expected_seconds: 60,
  fullSize: (src) => [src],
};
