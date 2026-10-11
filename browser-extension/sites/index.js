// 扩展支持的网站。新增网站：照 chatgpt.js 写一份 SiteSpec，加到这里，后端 sites/ 也要有同名的一份。
// claude 只做文本（网页文本模型），不接出图任务。能出视频的网站在 VIDEO_SITES。
import { CHATGPT } from './chatgpt.js';
import { CLAUDE } from './claude.js';
import { GEMINI } from './gemini.js';
import { MUSE } from './muse.js';

/** 出视频相关字段的默认值（和 backend/engines/browser/sites/base.py 的 SiteSpec 默认值一样）：不出视频的网站不用写。 */
export const VIDEO_DEFAULTS = {
  result_videos: '',
  video_src_hint: [],
  video_steps: [],
  video_active: '',
  video_dismiss: '',
  video_aspect_button: '',
  video_aspect_options: {},
  video_pending_patterns: [],
  video_expected_seconds: 180,
};

const withDefaults = (spec) => ({ ...VIDEO_DEFAULTS, ...spec });

export const SITES = Object.fromEntries([CHATGPT, GEMINI, MUSE, CLAUDE].map((s) => [s.key, withDefaults(s)]));
/** 能接出图任务的网站。 */
export const IMAGE_SITES = [CHATGPT.key, GEMINI.key, MUSE.key];
/** 能接出视频任务的网站。 */
export const VIDEO_SITES = [GEMINI.key, MUSE.key];
export { CHATGPT, CLAUDE, GEMINI, MUSE };
