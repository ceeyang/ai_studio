/*
 * Gemini 网页版（gemini.google.com/app）出图。和 backend/engines/browser/sites/gemini.py 是同一份选择器——
 * 网站改版时两边一起改（backend/tests/test_browser_extension_js.py 会比对两边是否一致）。
 *
 * 提示词开头写明「生成一张图」，Gemini 会自动用它的图像模型出图，通常几十秒。页面是 Angular，元素多是
 * 自定义标签（rich-textarea、model-response、user-query、generated-image），比 class 名稳定。
 *
 * 选择器改版时最容易坏的几项：
 * 1. prompt_box：rich-textarea 里的 Quill 编辑器 div.ql-editor（contenteditable）。
 * 2. upload_buttons：没有常驻的 file input，要先点「+」打开上传菜单；菜单里「上传文件」会弹文件选择框，
 *    扩展不能替用户选文件，所以点开菜单后再找 file input，找不到就往输入框里粘贴图片（Gemini 支持），
 *    再不行试拖放。第一次上传可能弹同意条款，dismiss_buttons 负责点掉。
 * 3. result_images：回复里 generated-image / single-image 下的 img，地址在 googleusercontent.com。
 *    页面里显示的是缩小图：Python 那边点「下载原尺寸图片」按钮拿原图；扩展拿不到浏览器下载的文件，
 *    改成把地址末尾的尺寸参数（=s1024、=w800-h600 …）换成 =s0 取原尺寸，不行再取页面里那张。
 * 4. 登录：Google 账号 cookie（__Secure-1PSID 等，域 .google.com）；没登录时页面右上角是「登录」链接。
 */
export const GEMINI = {
  key: 'gemini',
  label: 'Gemini',
  home: 'https://gemini.google.com/app',
  new_chat_url: 'https://gemini.google.com/app',
  host: 'gemini.google.com',
  hosts: ['gemini.google.com'],
  login_hosts: ['accounts.google.com'], // 标签页被跳到这些域名上 = 没登录
  cookie_url: 'https://gemini.google.com/',
  session_cookies: ['__Secure-1PSID', '__Secure-3PSID'],

  prompt_box:
    'rich-textarea div.ql-editor[contenteditable="true"], div.ql-editor[contenteditable="true"], ' +
    'div[role="textbox"][contenteditable="true"]',
  send_button:
    'button.send-button, button[aria-label="Send message"], button[aria-label="发送"], ' +
    'button[aria-label="发送消息"]',
  stop_button:
    'button[aria-label="Stop response"], button[aria-label="停止回答"], ' +
    'button[aria-label="停止生成"], button.send-button.stop',
  login_wall:
    'a[href*="accounts.google.com/ServiceLogin"], a[href*="accounts.google.com/v3/signin"], ' +
    'a[aria-label="Sign in"], a[aria-label="登录"]',
  file_input: 'input[type="file"]',
  upload_buttons: [
    'button[aria-label="Open upload file menu"], button[aria-label="打开文件上传菜单"], ' +
      'button.upload-card-button',
    'button[data-test-id="local-images-files-uploader-button"], button[aria-label*="Upload files"], ' +
      'button[aria-label*="上传文件"], [role="menuitem"]:has-text("Upload files"), ' +
      '[role="menuitem"]:has-text("上传文件")',
  ],
  attachment: 'uploader-file-preview, img[data-test-id="image-preview"], .file-preview-container img',
  attachment_busy: 'uploader-file-preview mat-progress-spinner, uploader-file-preview .loading',
  assistant_turn: 'model-response',
  result_images: 'model-response generated-image img, model-response single-image img, model-response img.image',
  user_images: 'user-query img, uploader-file-preview img',
  busy_marker: '',
  reply_exclude: '',
  // 网页文本模型读回复用（studio 驱动，见 backend/engines/browser/chat.py）：回复正文在 message-content 里
  text_reply: 'model-response message-content, model-response .model-response-text, model-response',
  text_exclude: 'model-thoughts, .thoughts-container, button, .code-block-decoration, .sr-only',
  dismiss_buttons:
    'mat-dialog-container button:has-text("Agree"), mat-dialog-container button:has-text("同意"), ' +
    'mat-dialog-container button:has-text("Got it"), mat-dialog-container button:has-text("知道了")',
  alert: 'mat-snack-bar-container, snack-bar-container, [role="alert"]',
  quota_patterns: [
    /reached (the|your) .{0,40}limit/i, /limit (for|on) (creating )?image/i, /try again later/i,
    /come back (later|tomorrow)/i, /too many requests/i, /达到.{0,12}(上限|限制)/i, /稍后再试/i,
    /明天再/i,
  ],
  refusal_patterns: [
    /can[’']?t (help|create|generate|make)/i, /(unable|not able) to (create|generate|help|make)/i,
    /just a language model/i, /我无法/i, /无法(生成|创建|制作|帮)/i, /只是一个语言模型/i,
    /不能(生成|创建|制作|帮)/i,
  ],
  image_src_hint: ['googleusercontent.com', 'blob:'],
  min_image_px: 384,
  ready_timeout: 45,
  upload_timeout: 90,
  settle_seconds: 4,
  no_image_grace: 12,
  echo_seconds: 5,
  expected_seconds: 60,
  /** 取原图时依次试的地址：googleusercontent 的尺寸参数换成 =s0（原尺寸），再试页面里那张。 */
  fullSize: (src) => {
    const m = /^(https:\/\/[^/]*googleusercontent\.com\/[^=?#]+)=[A-Za-z0-9_-]+$/.exec(src);
    return m ? [`${m[1]}=s0`, src] : [src];
  },
};
