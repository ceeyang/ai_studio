/*
 * 可灵网页版出视频：国内站 klingai.com、国际站 kling.ai。生成器页流程（lib/flow.js 的 runGenerator），
 * 只在扩展里有：后端 backend/engines/browser/sites/generators.py 只记名字、地址和登录 cookie，选择器只在这里。
 * 页面探查记录见 docs/notes/2026-10-12-jimeng-kling-web.md（可灵还没摸完）。
 *
 * 2026-10-12 实测过（国际站，已登录）：生成页 https://kling.ai/app/omni/new、tiptap 输入框、模型下拉按钮
 * .model-type-select（「VIDEO 3.0 Omni」）、设置按钮 .setting-select（「1080p · 5s」）、技能条 .selector__skills
 * （Elements / Image to Video / Transformation / …）、隐藏的 input.el-upload__input[type=file]
 * （accept .jpg,.jpeg,.png,.mp4,.mov）、生成按钮 button.button-pay（「Generate」）。
 * 国内站只实测了生成页地址 https://klingai.com/app/video/new（app.klingai.com/cn/... 会跳过来）。
 *
 * 改版时最容易坏的几项（标了「待实测」的是照常见写法猜的，第一次实跑时对着页面改）：
 * 1. 登录 cookie session_cookies、没登录时的登录按钮 login_wall（都是猜的，只用来提示，不会挡住流程）。
 * 2. 技能条里「图生视频」那一项的元素和文字（options 里「生成方式」），模型下拉展开后的选项元素。
 * 3. 设置弹层里画幅、时长选项的元素（.setting-select 点开后的面板）。
 * 4. 首帧上传：国际站的 input.el-upload__input；尾帧上传位还没摸到（先不支持尾帧）。
 * 5. 结果区 result_card / result_video / result_failed，新任务在哪一头（result_new_at）。
 * 6. 失败文案：quota_patterns（积分不足）、refusal_patterns（审核不通过）、failed_patterns。
 * 7. 国内站整套照国际站猜（同一套前端，文字是中文）。
 */

const BASE = {
  kind: 'generator',
  family: 'kling',
  ready_timeout: 45,
  upload_timeout: 90,
  settle_seconds: 3,
  video_expected_seconds: 300,

  prompt_box: 'div[role="textbox"].ProseMirror, .ProseMirror[contenteditable="true"]', // 实测（国际站）
  send_button: 'button.button-pay', // 实测（国际站「Generate」）
  stop_button: '',
  // 待实测
  login_wall:
    'button:text-is("登录"), button:text-is("Sign In"), button:text-is("Log in"), [class*="login-btn"], ' +
    '[class*="sign-in-button"]',
  captcha: '[class*="captcha"], [class*="slider-verify"], #captcha', // 待实测
  dismiss_buttons: 'button:text-is("我知道了"), button:text-is("知道了"), button:text-is("Got it"), button:text-is("OK")', // 待实测
  alert: '.el-message, .el-notification, [role="alert"]', // Element Plus 的提示条（待实测）
  credits: '', // 待实测：积分余额
  cost: '',

  options: [
    {
      // 技能条实测（.selector__skills）；里面每一项的元素待实测。选项直接摆在页面上，不用点开
      name: '生成方式', param: 'mode', open: '', match: 'startsWith',
      option: '.selector__skills [class*="item"], .selector__skills button, .selector__skills [role="tab"]',
    },
    {
      // 模型按钮实测（.model-type-select）；展开后的选项待实测
      name: '模型', param: 'model', open: '.model-type-select', panel: '[class*="model-type"][class*="popper"], .el-popper',
      option: '.el-popper [class*="model-item"], .el-popper .el-select-dropdown__item, .el-popper [role="option"]',
      match: 'startsWith',
    },
    {
      // 设置按钮实测（.setting-select，「1080p · 5s」）；弹层里画幅选项待实测
      name: '画幅', param: 'ratio', open: '.setting-select', panel: '.el-popper, [class*="setting-panel"]', match: 'exact',
      option:
        '[class*="setting-panel"] [class*="ratio"] [class*="item"], .el-popper [class*="ratio"] [class*="item"], ' +
        '.el-popper [class*="aspect"] [class*="item"], .el-popper .el-radio-button',
    },
  ],
  // 时长在同一个设置弹层里，点刻度（「5s」「10s」），没有输入框（待实测）
  duration: {
    name: '时长', open: '.setting-select', panel: '.el-popper, [class*="setting-panel"]',
    option:
      '[class*="setting-panel"] [class*="duration"] [class*="item"], .el-popper [class*="duration"] [class*="item"], ' +
      '.el-popper [class*="duration"] .el-radio-button',
  },
  upload: {
    first: { input: 'input.el-upload__input[type="file"], input[type="file"]', index: 0 }, // 实测（国际站）
    end: null, // 尾帧上传位还没摸到
    thumb: '[class*="upload"] img, [class*="image-preview"] img, [class*="reference"] img', // 待实测
    busy: '.el-upload-list__item.is-uploading, [class*="uploading"]', // 待实测
  },
  // 待实测（见文件头第 5 条）
  result_card: '[class*="creation-item"], [class*="video-item"], [class*="task-item"], [class*="feed-item"]',
  result_video: 'video',
  result_failed: '[class*="fail"], [class*="error"]',
  result_new_at: 'first',
  video_src_hint: [],

  quota_patterns: [
    /积分不足/i, /灵感值不足/i, /余额不足/i, /次数.{0,6}(上限|用完)/i, /今日.{0,8}上限/i,
    /insufficient credits/i, /not enough credits/i, /out of credits/i, /limit reached/i,
  ],
  refusal_patterns: [
    /不合规/i, /违规/i, /审核(未|没)?通过|未通过审核/i, /敏感/i,
    /violat/i, /community guidelines/i, /content policy/i, /inappropriate/i,
  ],
  failed_patterns: [/生成失败/i, /失败/i, /failed/i],
  fullSize: (src) => [src],
};

export const KLING_CN = {
  ...BASE,
  key: 'kling_cn',
  region: 'cn',
  label: '可灵',
  home: 'https://klingai.com/',
  generate_url: 'https://klingai.com/app/video/new', // 实测
  new_chat_url: 'https://klingai.com/app/video/new',
  host: 'klingai.com',
  hosts: ['klingai.com'],
  login_hosts: [],
  cookie_url: 'https://klingai.com/',
  session_cookies: ['kuaishou.ai.portal_st', 'userId'], // 待实测
  defaults: { mode: ['图生视频', '首帧'] }, // 待实测
};

export const KLING_GLOBAL = {
  ...BASE,
  key: 'kling_global',
  region: 'global',
  label: 'Kling',
  home: 'https://kling.ai/',
  generate_url: 'https://kling.ai/app/omni/new', // 实测
  new_chat_url: 'https://kling.ai/app/omni/new',
  host: 'kling.ai',
  hosts: ['kling.ai'],
  login_hosts: [],
  cookie_url: 'https://kling.ai/',
  session_cookies: ['ksi18n.ai.portal_st', 'userId'], // 待实测
  defaults: { mode: ['Image to Video'] }, // 实测的技能条文字
};
