/*
 * ChatGPT 网页版（chatgpt.com）出图。和 backend/engines/browser/sites/chatgpt.py 是同一份选择器——
 * 网站改版时两边一起改（backend/tests/test_browser_extension_js.py 会比对两边是否一致）。
 *
 * 出图走对话里的图片生成工具：提示词开头写明「直接生成一张图」，它就会调图片生成，一张通常 1–3 分钟，
 * 出图过程中会先显示模糊的半成品，停止按钮消失、图片地址不再变化才算出完。
 *
 * 选择器改版时最容易坏的几项（按顺序排查）：
 * 1. prompt_box：输入框是 ProseMirror 的 contenteditable，id 一直是 prompt-textarea；新界面是
 *    textarea#mobile-composer-prompt（占位「询问 ChatGPT」）。
 * 2. send_button / stop_button：靠 data-testid 和 aria-label，偶尔会改名。
 * 3. result_images：生成的图在助手那条消息里，地址是 chatgpt.com/backend-api/estuary/content?id=file_…
 *    （image_src_hint 用它排除头像、图标）；用户上传的参考图也是这个地址，靠 user_images 和
 *    「提交后 echo_seconds 秒内冒出来的图是回显的参考图」两条规则排除。
 * 4. login_wall：没登录时有 data-testid=login-button 的「登录」按钮，新界面到处是文字为「登录」的按钮。
 *    登录 cookie 是 __Secure-next-auth.session-token（太长时拆成 .0/.1 两段）；扩展只看它在不在，不读值。
 *
 * 参考图上传：先找隐藏的 input[type=file][accept*=image] 直接塞文件（DataTransfer）；没有就点「+」
 * 菜单再找；还不行就往输入框里粘贴图片，最后试拖放。扩展不能替用户在文件选择框里选文件，所以不点
 * 会弹文件选择框的那个按钮（upload_buttons 的最后一个）。
 * 取原图：在页面里带 cookie fetch 图片地址（同源），失败再由扩展直接取（有 host 权限，不受 CORS 限制）。
 */
export const CHATGPT = {
  key: 'chatgpt',
  label: 'ChatGPT',
  home: 'https://chatgpt.com/',
  new_chat_url: 'https://chatgpt.com/',
  host: 'chatgpt.com',
  hosts: ['chatgpt.com', 'chat.openai.com'], // 找标签页时这些域名都算
  login_hosts: ['auth.openai.com', 'auth0.openai.com'], // 标签页被跳到这些域名上 = 没登录
  cookie_url: 'https://chatgpt.com/',
  session_cookies: ['__Secure-next-auth.session-token', '__Secure-next-auth.session-token.0'],

  prompt_box:
    '#prompt-textarea, div.ProseMirror[contenteditable="true"], ' +
    'textarea[id$="composer-prompt"], textarea[placeholder*="ChatGPT"]',
  send_button:
    'button[data-testid="send-button"], #composer-submit-button, ' +
    'button[aria-label="发送消息"], button[aria-label="Send message"], ' +
    'button[aria-label="Send prompt"], button[aria-label="发送提示"]',
  stop_button:
    'button[data-testid="stop-button"], button[aria-label="Stop streaming"], ' +
    'button[aria-label="停止流式传输"], button[aria-label="停止"], button[aria-label="Stop"], ' +
    'button[aria-label*="停止生成"], button[aria-label*="Stop generating"]',
  login_wall:
    '[data-testid="login-button"], [data-testid="signup-button"], ' +
    'button:text-is("登录"), button:text-is("Log in")',
  // 输入框旁边有几个隐藏的 file input（照片/文件/相机），都收图片；取第一个收图片的
  file_input: 'input[type="file"][accept*="image"]',
  upload_buttons: [
    'button[data-testid="composer-plus-btn"], button[aria-label="Add photos and files"], ' +
      'button[aria-label="添加照片和文件"], button[aria-label="添加文件等"], ' +
      'button[aria-label="Add files and more"]',
    '[role="menuitem"]:has-text("Add photos"), [role="menuitem"]:has-text("添加照片"), ' +
      'button:text-is("照片"), button:text-is("Photos")',
  ],
  attachment: 'form img',
  attachment_busy: 'form [role="progressbar"], form .animate-spin',
  assistant_turn: 'article[data-turn="assistant"], [data-message-author-role="assistant"]',
  result_images:
    'article[data-turn="assistant"] img, [data-message-author-role="assistant"] img, ' +
    'img[alt="Generated image"], img[alt*="已生成图片"], img[alt*="生成的图片"], ' +
    // 新界面的消息结构没实测过：兜底取页面里所有 estuary 地址的图（参考图靠 user_images 和回显规则排除）
    'main img[src*="/backend-api/estuary/content"]',
  user_images: 'article[data-turn="user"] img, [data-message-author-role="user"] img, form img',
  dismiss_buttons:
    '[role="dialog"] button:has-text("Got it"), [role="dialog"] button:has-text("知道了"), ' +
    '[role="dialog"] button:has-text("Okay, let’s go")',
  alert: '[role="alert"]',
  quota_patterns: [
    /hit (the|your) .{0,40}limit/i, /reached (the|your|our) .{0,40}limit/i,
    /limit (for|on) image/i, /rate limit/i, /too many requests/i,
    /try again (later|in \d|after)/i, /达到.{0,12}(上限|限制)/i, /次数.{0,6}(用完|上限)/i,
    /请求(过多|太多)/i, /稍后再试/i,
  ],
  refusal_patterns: [
    /can[’']?t (help|create|generate|make|produce)/i, /(unable|not able) to (create|generate|help|make)/i,
    /content polic/i, /violat/i, /won[’']?t be able/i, /无法(生成|创建|制作|帮|提供|满足)/i,
    /不能(生成|创建|制作|帮)/i, /违反/i, /内容政策/i,
  ],
  image_src_hint: ['/backend-api/estuary/content', 'oaiusercontent.com', '/backend-api/files'],
  min_image_px: 384,
  ready_timeout: 45,
  upload_timeout: 90,
  settle_seconds: 5,
  no_image_grace: 15,
  echo_seconds: 5,
  expected_seconds: 120,
  /** 取原图时依次试的地址：ChatGPT 页面里的就是原图。 */
  fullSize: (src) => [src],
};
