/*
 * 即梦（jimeng.jianying.com，国内）/ Dreamina（dreamina.capcut.com，海外）网页版出视频（Seedance）。
 * 生成器页流程（lib/flow.js 的 runGenerator），只在扩展里有：后端 backend/engines/browser/sites/generators.py
 * 只记名字、地址和登录 cookie（backend/tests/test_browser_extension_js.py 比对两边），选择器只在这里。
 * 页面探查记录见 docs/notes/2026-10-12-jimeng-kling-web.md。
 *
 * 2026-10-12 连作者的 Chrome 实测过（国内站）：生成页地址、登录 cookie、tiptap 输入框、三个 Arco 下拉
 * （创作类型 / 模型 / 参考方式，选项 .lv-select-option）、画幅按钮（「16:9 720P 1」，点开 .lv-popover）、
 * 时长按钮（「5s」，弹层里有滑块和数字输入框，4–15 秒整数）、积分余额和这次要花的积分。
 *
 * 改版时最容易坏的几项（标了「待实测」的是照常见写法猜的，第一次实跑时对着页面改）：
 * 1. 首帧 / 尾帧上传位 upload：猜的是「首尾帧」模式下生成框里的两个隐藏 input[type=file]（第 1 个首帧、
 *    第 2 个尾帧），首帧不行再粘贴进输入框；缩略图 upload.thumb、上传中 upload.busy 也是猜的。
 * 2. 生成按钮 send_button：生成框右下的箭头按钮。
 * 3. 结果区 result_card / result_video / result_failed，以及新任务出现在哪一头（result_new_at）。
 * 4. 失败文案：quota_patterns（积分不足）、refusal_patterns（审核不通过、真人脸）、failed_patterns。
 * 5. 画幅弹层里的比例选项（options 里「画幅」的 option）、时长弹层的数字输入框（duration.input）。
 * 6. 海外站（Dreamina）整套都没实测：按同一套 Arco 组件（前缀 lv-）猜，文字写了英文界面的备选。
 */

const SELECT = '[role="combobox"].lv-select'; // 实测
const SELECT_OPTION = '.lv-select-option'; // 实测（灰的带 lv-select-option-wrapper-disabled）

const BASE = {
  kind: 'generator',
  family: 'jimeng',
  ready_timeout: 45,
  upload_timeout: 90,
  settle_seconds: 3,
  video_expected_seconds: 240,

  prompt_box: 'div[role="textbox"].ProseMirror', // 实测（tiptap）
  // 待实测：生成框右下的箭头按钮
  send_button:
    'button[class*="submit-button"], button[class*="submit"], [class*="send-button"], button[class*="generate-button"]',
  stop_button: '',
  // 待实测：没登录时的登录按钮（:text-is 完全相等，免得命中「退出登录」）
  login_wall: 'button:text-is("登录"), button:text-is("Log in"), button:text-is("Sign in"), [class*="login-button"]',
  captcha: '#captcha_container, [class*="captcha"], [class*="verify-bar"], [class*="secsdk"]', // 待实测
  dismiss_buttons: 'button:text-is("我知道了"), button:text-is("知道了"), button:text-is("Got it")', // 待实测
  alert: '.lv-message, .lv-notification, [role="alert"]', // Arco 的提示条（待实测）
  credits: '[class*="credit-amount-text"]', // 实测：右上角积分余额
  cost: '[class*="actual-credits"]', // 实测：生成框右下这次要花的积分

  options: [
    {
      name: '创作类型', param: 'tab', open: SELECT, option: SELECT_OPTION, match: 'startsWith', // 实测
      openMatch: '^(Agent|图片生成|视频生成|音乐生成|音频生成|数字人|动作模仿|AI Agent|Image|Video|Music|Audio|Avatar|Motion)',
    },
    {
      name: '模型', param: 'model', open: SELECT, option: SELECT_OPTION, match: 'startsWith', // 实测
      openMatch: '(Seedance|HappyHorse|Wan|即梦)',
    },
    {
      name: '参考方式', param: 'mode', open: SELECT, option: SELECT_OPTION, match: 'startsWith', // 实测
      openMatch: '(全能参考|首尾帧|智能多帧|智能编辑|超长视频|reference|frames?|multi)',
    },
    {
      // 画幅按钮实测（文字形如「16:9 720P 1」）；弹层里比例选项的元素待实测，按文字完全相等认
      name: '画幅', param: 'ratio', open: 'button.lv-btn', openMatch: '\\d+:\\d+', panel: '.lv-popover', match: 'exact',
      option:
        '.lv-popover [role="radio"], .lv-popover label, .lv-popover button, .lv-popover [class*="ratio"] div, ' +
        '.lv-popover [class*="ratio"] span',
    },
  ],
  // 时长按钮实测（文字形如「5s」），弹层里滑块 + 数字输入框（.lv-input-number 里的 input，待实测能不能直接填）
  duration: {
    name: '时长', open: 'button.lv-btn', openMatch: '^\\d+\\s*s$', panel: '[class*="duration"], .lv-popover',
    input: '.lv-popover .lv-input-number input, [class*="duration"] .lv-input-number input',
    option: '[class*="duration"] [class*="mark"], .lv-popover .lv-slider-mark-text', // 待实测：刻度
  },
  // 待实测（见文件头第 1 条）
  upload: {
    first: { input: 'input[type="file"]', index: 0, paste: true },
    end: { input: 'input[type="file"]', index: 1 },
    thumb: '[class*="reference"] img, [class*="upload"] img, [class*="frame"] img',
    busy: '[class*="uploading"], [class*="upload"] [class*="loading"]',
  },
  // 待实测（见文件头第 3 条）：新版是「创作」对话式页面，新任务在最下面
  result_card: '[class*="video-record"], [class*="record-item"], [class*="task-item"], [class*="item-card"][data-id]',
  result_video: 'video',
  result_failed: '[class*="fail"], [class*="error-tip"]',
  result_new_at: 'last',
  video_src_hint: [],

  // 待实测（见文件头第 4 条）
  quota_patterns: [
    /积分不足/i, /积分不够/i, /额度不足/i, /次数.{0,6}(上限|用完)/i, /今日.{0,8}上限/i,
    /insufficient credits/i, /not enough credits/i, /out of credits/i, /limit reached/i,
  ],
  refusal_patterns: [
    /不合规/i, /违规/i, /审核(未|没)?通过|未通过审核/i, /敏感/i, /不支持真人/i, /真人人脸/i,
    /violat/i, /community guidelines/i, /content policy/i, /real (human )?faces?/i,
  ],
  failed_patterns: [/生成失败/i, /失败/i, /failed/i, /排队超时/i],
  /** 取原视频的地址：卡片里 <video> 的地址就是原视频（待实测有没有更清晰的下载地址）。 */
  fullSize: (src) => [src],
};

export const JIMENG_CN = {
  ...BASE,
  key: 'jimeng_cn',
  region: 'cn',
  label: '即梦',
  home: 'https://jimeng.jianying.com/',
  generate_url: 'https://jimeng.jianying.com/ai-tool/generate?type=video', // 实测
  new_chat_url: 'https://jimeng.jianying.com/ai-tool/generate?type=video',
  host: 'jimeng.jianying.com',
  hosts: ['jimeng.jianying.com'],
  login_hosts: [], // 待实测：没登录时会不会跳到别的域名
  cookie_url: 'https://jimeng.jianying.com/',
  session_cookies: ['sessionid', 'sid_tt', 'uid_tt'], // 实测（域 jianying.com）
  defaults: { tab: '视频生成', mode: '首尾帧' }, // 实测的选项文字
};

export const JIMENG_GLOBAL = {
  ...BASE,
  key: 'jimeng_global',
  region: 'global',
  label: 'Dreamina',
  home: 'https://dreamina.capcut.com/',
  generate_url: 'https://dreamina.capcut.com/ai-tool/generate?type=video', // 待实测：照国内站的路径猜
  new_chat_url: 'https://dreamina.capcut.com/ai-tool/generate?type=video',
  host: 'dreamina.capcut.com',
  hosts: ['dreamina.capcut.com'],
  login_hosts: [],
  cookie_url: 'https://dreamina.capcut.com/',
  session_cookies: ['sessionid', 'sid_tt', 'uid_tt'], // 待实测：照国内站猜（字节系同一套登录）
  // 待实测：英文界面的写法，中文界面的也留着
  defaults: {
    tab: ['Video generation', 'Video', '视频生成'],
    mode: ['First and last frames', 'First & last frames', 'First/last frames', '首尾帧'],
  },
};
