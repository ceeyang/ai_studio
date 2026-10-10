/*
 * 和 studio 之间的 WebSocket 协议用到的纯函数（没有 chrome.* 调用，node 里能直接单测）。
 * 协议本身见 backend/engines/browser/extension.py 的文件头。
 */

export const DEFAULT_SERVER = 'ws://127.0.0.1:8700/api/browser/ext';
export const PING_SECONDS = 20; // MV3 的 service worker 空闲 30 秒会被挂起；WebSocket 有收发就不会
export const CHUNK_CHARS = 512 * 1024; // 图片 base64 分块大小：远小于 uvicorn 默认的 16MB 单帧上限
export const REPLACED_CODE = 4000; // studio 说「有新的扩展连接替换了你」：别马上重连，免得两个浏览器来回抢

/** 断线重连的等待毫秒数：1s、2s、4s … 封顶 30s，加 0–20% 抖动。 */
export function backoffMs(attempt, rand = Math.random) {
  const base = Math.min(30000, 1000 * 2 ** Math.max(0, Math.min(attempt, 10)));
  return Math.round(base * (1 + 0.2 * rand()));
}

/** 把一张图拆成 image 消息（每块 CHUNK_CHARS 个 base64 字符）。 */
export function imageMessages(jobId, index, img, chunk = CHUNK_CHARS) {
  const b64 = img.b64 || '';
  const total = Math.max(1, Math.ceil(b64.length / chunk));
  const out = [];
  for (let seq = 0; seq < total; seq++) {
    out.push({
      type: 'image', job_id: jobId, index, seq, total,
      mime: img.mime || '', src: img.src || '', via: img.via || 'page',
      data: b64.slice(seq * chunk, (seq + 1) * chunk),
    });
  }
  return out;
}

/** 按文件头判断 base64 里是不是图片，返回 mime 或 ''。 */
export function sniffMime(b64) {
  if (!b64 || typeof b64 !== 'string') return '';
  let head;
  try {
    head = atob(b64.slice(0, 24));
  } catch (_e) {
    return '';
  }
  const at = (i) => head.charCodeAt(i);
  if (at(0) === 0x89 && head.slice(1, 4) === 'PNG') return 'image/png';
  if (at(0) === 0xff && at(1) === 0xd8 && at(2) === 0xff) return 'image/jpeg';
  if (head.slice(0, 4) === 'RIFF' && head.slice(8, 12) === 'WEBP') return 'image/webp';
  if (head.slice(0, 4) === 'GIF8') return 'image/gif';
  return '';
}

/** ArrayBuffer → base64（分段拼，避免大图把参数栈撑爆）。 */
export function bufferToB64(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

/** 网址属于这个网站吗（看 spec.hosts）。 */
export function onSite(url, spec) {
  try {
    const h = new URL(url).hostname;
    return spec.hosts.some((x) => h === x || h.endsWith('.' + x));
  } catch (_e) {
    return false;
  }
}

/**
 * 出图用哪个标签页（只排序，忙不忙由调用方再探一下页面）：
 * 1. 扩展上次出图用过、还开着并且还在这个网站上的那个；
 * 2. reuseUserTabs 时：用户开着的这个网站的其他标签页，不是当前窗口正在看的那个，最久没碰过的优先；
 * 都没有就由调用方在后台新开一个。
 */
export function rankTabs(tabs, spec, { studioTabId = null, reuseUserTabs = true } = {}) {
  const mine = tabs.filter((t) => t.url && onSite(t.url, spec));
  const out = [];
  const studio = mine.find((t) => t.id === studioTabId);
  if (studio) out.push(studio);
  if (reuseUserTabs) {
    const others = mine
      .filter((t) => t.id !== studioTabId && !t.active)
      .sort((a, b) => (a.lastAccessed || 0) - (b.lastAccessed || 0));
    out.push(...others);
  }
  return out;
}

/** 综合 cookie 和页面探测判断是否登录：页面上有登录按钮就算没登录；否则有 cookie 或页面有输入框就算登录。 */
export function loggedIn(cookie, page) {
  if (page === 'login_wall') return false;
  return !!cookie || page === 'logged_in';
}
