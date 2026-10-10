// 扩展支持的网站。新增网站：照 chatgpt.js 写一份 SiteSpec，加到这里，后端 sites/ 也要有同名的一份。
import { CHATGPT } from './chatgpt.js';
import { GEMINI } from './gemini.js';

export const SITES = { [CHATGPT.key]: CHATGPT, [GEMINI.key]: GEMINI };
export { CHATGPT, GEMINI };
