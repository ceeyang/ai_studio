// 扩展的纯函数：重连退避、分块、图片类型、选标签页、登录判断、Gemini 原尺寸地址。
import { describe, expect, it } from 'vitest';
import {
  backoffMs, imageMessages, loggedIn, onSite, rankTabs, sniffMime,
} from '../../../browser-extension/lib/protocol.js';
import { CHATGPT, GEMINI, SITES } from '../../../browser-extension/sites/index.js';
import { PNG_B64 } from './fakePage.js';

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
    expect(Object.keys(SITES)).toEqual(['chatgpt', 'gemini']);
  });
});
