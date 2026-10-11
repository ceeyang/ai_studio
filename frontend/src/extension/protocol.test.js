// 扩展的纯函数：重连退避、分块、图片类型、选标签页、登录判断、Gemini 原尺寸地址。
import { describe, expect, it } from 'vitest';
import {
  FEATURES, RPC_DOM_OPS, backoffMs, imageMessages, loggedIn, onSite, rankTabs, rpcProblem, siteOfUrl,
  sniffMime,
} from '../../../browser-extension/lib/protocol.js';
import { CHATGPT, CLAUDE, GEMINI, IMAGE_SITES, MUSE, SITES, VIDEO_SITES } from '../../../browser-extension/sites/index.js';
import { MP4_B64, PNG_B64 } from './fakePage.js';

describe('extension protocol helpers', () => {
  it('重连退避：1s 起翻倍，封顶 30s，抖动不超过 20%', () => {
    expect(backoffMs(0, () => 0)).toBe(1000);
    expect(backoffMs(3, () => 0)).toBe(8000);
    expect(backoffMs(20, () => 0)).toBe(30000);
    expect(backoffMs(20, () => 1)).toBe(36000);
  });

  it('图片按块拆成 image 消息，拼回来不变', () => {
    const b64 = 'A'.repeat(2500);
    const msgs = imageMessages('j1', 2, { b64, mime: 'image/png', src: 's', via: 'page' }, 1000);
    expect(msgs).toHaveLength(3);
    expect(msgs.every((m) => m.type === 'image' && m.job_id === 'j1' && m.index === 2 && m.total === 3)).toBe(true);
    expect(msgs.map((m) => m.data).join('')).toBe(b64);
    expect(imageMessages('j', 0, { b64: '' })).toHaveLength(1);
  });

  it('按文件头认图片', () => {
    expect(sniffMime(PNG_B64)).toBe('image/png');
    expect(sniffMime(btoa('\xff\xd8\xff\xe0abc'))).toBe('image/jpeg');
    expect(sniffMime(btoa('RIFF\0\0\0\0WEBPVP8 '))).toBe('image/webp');
    expect(sniffMime(btoa('<html>'))).toBe('');
    expect(sniffMime('!!!')).toBe('');
    expect(sniffMime(undefined)).toBe('');
  });

  it('网址属于哪个网站', () => {
    expect(onSite('https://chatgpt.com/c/1', CHATGPT)).toBe(true);
    expect(onSite('https://chat.openai.com/', CHATGPT)).toBe(true);
    expect(onSite('https://evilchatgpt.com/', CHATGPT)).toBe(false);
    expect(onSite('https://gemini.google.com/app/x', GEMINI)).toBe(true);
    expect(onSite('chrome://newtab', GEMINI)).toBe(false);
  });

  it('选标签页：上次用过的优先，其次没在看的、最久没碰的；正在看的不借', () => {
    const tabs = [
      { id: 1, url: 'https://chatgpt.com/c/a', active: true, lastAccessed: 9 },
      { id: 2, url: 'https://chatgpt.com/c/b', active: false, lastAccessed: 5 },
      { id: 3, url: 'https://chatgpt.com/', active: false, lastAccessed: 1 },
      { id: 4, url: 'https://gemini.google.com/app', active: false, lastAccessed: 0 },
      { id: 5, url: 'https://example.com/', active: false },
    ];
    expect(rankTabs(tabs, CHATGPT, { studioTabId: 2 }).map((t) => t.id)).toEqual([2, 3]);
    expect(rankTabs(tabs, CHATGPT, {}).map((t) => t.id)).toEqual([3, 2]);
    expect(rankTabs(tabs, CHATGPT, { studioTabId: 1 }).map((t) => t.id)).toEqual([1, 3, 2]);
    expect(rankTabs(tabs, CHATGPT, { studioTabId: 2, reuseUserTabs: false }).map((t) => t.id)).toEqual([2]);
    expect(rankTabs(tabs, CHATGPT, { reuseUserTabs: false })).toEqual([]);
    expect(rankTabs(tabs, GEMINI, {}).map((t) => t.id)).toEqual([4]);
    // 写文本和出图共用标签页：按给的顺序先用 studio 用过的（这次用途的、另一种用途的），再借用户闲着的
    expect(rankTabs(tabs, CHATGPT, { studioTabIds: [2, 1] }).map((t) => t.id)).toEqual([2, 1, 3]);
    expect(rankTabs(tabs, CHATGPT, { studioTabIds: [99, 1] }).map((t) => t.id)).toEqual([1, 3, 2]);   // 99 关掉了
    expect(rankTabs(tabs, CHATGPT, { studioTabIds: [1, 1] }).map((t) => t.id)).toEqual([1, 3, 2]);    // 两种用途是同一个
  });

  it('登录判断：登录按钮优先，其次 cookie 或页面输入框', () => {
    expect(loggedIn(true, 'login_wall')).toBe(false);
    expect(loggedIn(true, null)).toBe(true);
    expect(loggedIn(false, 'logged_in')).toBe(true);
    expect(loggedIn(false, 'unknown')).toBe(false);
    expect(loggedIn(null, null)).toBe(false);
  });

  it('Gemini 原尺寸地址；ChatGPT 原样', () => {
    expect(GEMINI.fullSize('https://lh3.googleusercontent.com/gg/AbC-d_e=s1024-rj')).toEqual([
      'https://lh3.googleusercontent.com/gg/AbC-d_e=s0', 'https://lh3.googleusercontent.com/gg/AbC-d_e=s1024-rj',
    ]);
    expect(GEMINI.fullSize('blob:https://gemini.google.com/1')).toEqual(['blob:https://gemini.google.com/1']);
    expect(CHATGPT.fullSize('x')).toEqual(['x']);
    expect(Object.keys(SITES)).toEqual(['chatgpt', 'gemini', 'muse', 'claude']);
  });

  it('rpc（网页文本模型）：hello 报 rpc 能力；只许对交出去的标签页、白名单里的页面操作、支持的网站', () => {
    expect(FEATURES).toContain('rpc');
    expect(RPC_DOM_OPS).toContain('replyText');
    expect(IMAGE_SITES).toEqual(['chatgpt', 'gemini', 'muse']);
    expect(siteOfUrl('https://claude.ai/new', SITES)).toBe('claude');
    expect(siteOfUrl('https://evil.example/', SITES)).toBe('');
    const ctx = { tabs: new Set([5]), sites: SITES };
    expect(rpcProblem('acquire_tab', { site: 'claude' }, ctx)).toBe('');
    expect(rpcProblem('acquire_tab', { site: 'bing' }, ctx)).toMatch('不认识网站');
    expect(rpcProblem('dom', { tab_id: 5, op: 'replyText' }, ctx)).toBe('');
    expect(rpcProblem('dom', { tab_id: 5, op: 'fetchB64' }, ctx)).toMatch('不允许');
    expect(rpcProblem('dom', { tab_id: 6, op: 'visible' }, ctx)).toMatch('不是扩展给 studio 用的');
    expect(rpcProblem('goto', { tab_id: 5, url: 'https://claude.ai/new' }, ctx)).toBe('');
    expect(rpcProblem('goto', { tab_id: 5, url: 'https://evil.example/' }, ctx)).toMatch('只能打开');
    expect(rpcProblem('cookie_names', { url: 'https://evil.example/' }, ctx)).toMatch('只能查');
    expect(rpcProblem('eval', {}, ctx)).toMatch('不认识 rpc 方法');
    expect(CLAUDE.new_chat_url).toBe('https://claude.ai/new');
  });

  it('认 mp4；视频网站和能力', () => {
    expect(sniffMime(MP4_B64)).toBe('video/mp4');
    expect(VIDEO_SITES).toEqual(['gemini', 'muse']);
    expect(FEATURES).toEqual(expect.arrayContaining(['rpc', 'conversation', 'video']));
    expect(SITES.chatgpt.result_videos).toBe(''); // 不出视频的网站有默认值
    expect(SITES.muse.video_steps).toEqual([]);
  });
});
