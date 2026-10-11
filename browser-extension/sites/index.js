// 扩展支持的网站。新增网站：照 chatgpt.js 写一份 SiteSpec，加到这里，后端 sites/ 也要有同名的一份。
// claude 只做文本（网页文本模型），不接出图任务。能出视频的网站在 VIDEO_SITES。
// 生成器页网站（即梦 / 可灵，v0.5 起，每个区域一个 key）在 GENERATOR_SITES：选择器只在扩展里，
// 后端 backend/engines/browser/sites/generators.py 只记名字、地址和登录 cookie。
import { CHATGPT } from './chatgpt.js';
import { CLAUDE } from './claude.js';
import { GEMINI } from './gemini.js';
import { JIMENG_CN, JIMENG_GLOBAL } from './jimeng.js';
import { KLING_CN, KLING_GLOBAL } from './kling.js';
import { MUSE } from './muse.js';

/** 出视频相关字段的默认值（和 backend/engines/browser/sites/base.py 的 SiteSpec 默认值一样）：不出视频的网站不用写。 */
export const VIDEO_DEFAULTS = {
  result_videos: '',
  video_src_hint: [],
  video_steps: [],
  video_active: '',
  video_dismiss: '',
  video_new_chat: '',
  video_aspect_button: '',
  video_aspect_options: {},
  video_pending_patterns: [],
  video_expected_seconds: 180,
};

const withDefaults = (spec) => ({ ...VIDEO_DEFAULTS, ...spec });

/** 生成器页网站（spec.kind = 'generator'，走 lib/flow.js 的 runGenerator）。 */
export const GENERATORS = Object.fromEntries([JIMENG_CN, JIMENG_GLOBAL, KLING_CN, KLING_GLOBAL].map((s) => [s.key, s]));
export const GENERATOR_SITES = Object.keys(GENERATORS);

export const SITES = {
  ...Object.fromEntries([CHATGPT, GEMINI, MUSE, CLAUDE].map((s) => [s.key, withDefaults(s)])),
  ...GENERATORS,
};
/** 能接出图任务的网站。 */
export const IMAGE_SITES = [CHATGPT.key, GEMINI.key, MUSE.key];
/** 能接出视频任务的网站（聊天页流程）；生成器页的在 GENERATOR_SITES。 */
export const VIDEO_SITES = [GEMINI.key, MUSE.key];
export { CHATGPT, CLAUDE, GEMINI, JIMENG_CN, JIMENG_GLOBAL, KLING_CN, KLING_GLOBAL, MUSE };
