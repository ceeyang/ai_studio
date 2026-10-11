// 扩展支持的网站。新增网站：照 chatgpt.js 写一份 SiteSpec，加到这里，后端 sites/ 也要有同名的一份。
// claude 只做文本（网页文本模型），不接出图任务。
import { CHATGPT } from './chatgpt.js';
import { CLAUDE } from './claude.js';
import { GEMINI } from './gemini.js';

export const SITES = { [CHATGPT.key]: CHATGPT, [GEMINI.key]: GEMINI, [CLAUDE.key]: CLAUDE };
/** 能接出图任务的网站。 */
export const IMAGE_SITES = [CHATGPT.key, GEMINI.key];
export { CHATGPT, CLAUDE, GEMINI };
