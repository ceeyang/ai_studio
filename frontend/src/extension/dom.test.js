// 页面里的 DOM 助手（browser-extension/content/dom.js）：Playwright 风格的选择器、可见性、取文字和图片、输入。
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import '../../../browser-extension/content/dom.js';
import { CHATGPT, GEMINI } from '../../../browser-extension/sites/index.js';

let D;
beforeAll(() => {
  globalThis.__aiStudioDomNoLayout = true; // jsdom 没有布局
  D = globalThis.__aiStudioDom;
});
beforeEach(() => {
  document.body.innerHTML = '';
});

describe('content/dom.js', () => {
  it('按顶层逗号拆选择器，引号和括号里的逗号不拆', () => {
    expect(D.splitList('a, b[x="1,2"], c:has-text("a, b"), d:not(.x, .y)')).toEqual([
      'a', 'b[x="1,2"]', 'c:has-text("a, b")', 'd:not(.x, .y)',
    ]);
    expect(D.parsePart('[role="dialog"] button:has-text("Got it")')).toEqual({
      base: '[role="dialog"] button', filters: [{ kind: 'has-text', text: 'Got it' }],
    });
    expect(D.parsePart(':text-is("登录")').base).toBe('*');
  });

  it(':has-text 不分大小写包含，:text-is 完全相等；按文档顺序去重；写错的备选跳过', () => {
    document.body.innerHTML = `
      <button id="a"> 登录 </button><button id="b">登录后使用</button>
      <div role="dialog"><button id="c">OK, GOT IT</button></div>
      <button id="d" data-testid="login-button">Log in</button>`;
    expect(D.queryAll(CHATGPT.login_wall).map((e) => e.id)).toEqual(['a', 'd']);
    expect(D.queryAll(CHATGPT.dismiss_buttons).map((e) => e.id)).toEqual(['c']);
    expect(D.queryAll('button:text-is("登录"), ::::bad, #a').map((e) => e.id)).toEqual(['a']);
    expect(D.visible(CHATGPT.login_wall)).toBe(true);
    expect(D.count('button')).toBe(4);
  });

  it('display:none 的不算可见；enabled 看 disabled 和 aria-disabled', () => {
    document.body.innerHTML = `
      <button data-testid="send-button" disabled>发</button>
      <button aria-label="发送消息" aria-disabled="true">发</button>
      <button data-testid="stop-button" style="display:none">停</button>`;
    expect(D.visible(CHATGPT.send_button)).toBe(true);
    expect(D.enabled(CHATGPT.send_button)).toBe(false);
    expect(D.visible(CHATGPT.stop_button)).toBe(false);
  });

  it('取最后一条回复的文字、回复里的图', () => {
    document.body.innerHTML = `
      <model-response>第一条</model-response>
      <model-response>第二条<generated-image><img src="https://lh3.googleusercontent.com/x=s1024"></generated-image></model-response>
      <user-query><img src="blob:ref"></user-query>`;
    expect(D.text(GEMINI.assistant_turn)).toContain('第二条');
    const imgs = D.images(GEMINI.result_images);
    expect(imgs).toHaveLength(1);
    expect(imgs[0].src).toBe('https://lh3.googleusercontent.com/x=s1024');
    expect(D.images(GEMINI.user_images).map((i) => i.src)).toEqual(['blob:ref']);
  });

  it('textarea 输入（execCommand 不可用时写 value 并发 input 事件）', () => {
    document.body.innerHTML = '<textarea id="mobile-composer-prompt" placeholder="询问 ChatGPT"></textarea>';
    const ta = document.querySelector('textarea');
    let fired = 0;
    ta.addEventListener('input', () => fired++);
    expect(D.typeText(CHATGPT.prompt_box, 'Generate one image now')).toBe(true);
    expect(ta.value).toBe('Generate one image now');
    expect(fired).toBeGreaterThan(0);
  });

  it('contenteditable 输入，找不到输入框返回 false', () => {
    document.body.innerHTML = '<rich-textarea><div class="ql-editor" contenteditable="true"><p><br></p></div></rich-textarea>';
    expect(D.typeText(GEMINI.prompt_box, '画一张图')).toBe(true);
    expect(document.querySelector('.ql-editor').textContent).toContain('画一张图');
    document.body.innerHTML = '';
    expect(D.typeText(GEMINI.prompt_box, 'x')).toBe(false);
    expect(D.click(GEMINI.send_button)).toBe(false);
  });

  it('click 发整套指针事件', () => {
    document.body.innerHTML = '<button data-testid="composer-plus-btn">+</button>';
    const seen = [];
    const b = document.querySelector('button');
    for (const t of ['pointerdown', 'mousedown', 'mouseup', 'click']) b.addEventListener(t, () => seen.push(t));
    expect(D.click(CHATGPT.upload_buttons[0])).toBe(true);
    expect(seen).toEqual(['pointerdown', 'mousedown', 'mouseup', 'click']);
  });

  it('probe：登录按钮、输入框、是否在生成、输入框里有没有草稿', () => {
    document.body.innerHTML = '<div id="prompt-textarea" contenteditable="true">写了一半</div><button data-testid="stop-button">停</button>';
    expect(D.probe(CHATGPT)).toMatchObject({ loginWall: false, prompt: true, busy: true, draft: true });
  });
});
