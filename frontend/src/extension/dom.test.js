// 页面里的 DOM 助手（browser-extension/content/dom.js）：Playwright 风格的选择器、可见性、取文字和图片、输入。
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import '../../../browser-extension/content/dom.js';
import { CHATGPT, CLAUDE, GEMINI } from '../../../browser-extension/sites/index.js';

let D;
beforeAll(() => {
  globalThis.__aiStudioDomNoLayout = true; // jsdom 没有布局
  D = globalThis.__aiStudioDom;
});
beforeEach(() => {
  document.body.innerHTML = '';
});

describe('content/dom.js', () => {
  it('ChatGPT 2026-10 新界面：结果图在 generated-image-gallery 里、地址是 blob:，用户气泡里的图不算；这一轮没完成算忙', () => {
    // 结构照 2026-10-11 实测的 chatgpt.com 页面（没有 article / data-message-author-role 了）
    document.body.innerHTML = `
      <main><div data-turn-key="k1"><div data-talvt-turn-state="streaming">
        <div data-user-message-bubble=""><img id="ref" alt="" src="blob:https://chatgpt.com/ref-1"></div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div data-testid="generated-image-gallery"><div><button data-testid="generated-image-preview" aria-label="Generated image 1">
          <img id="out" alt="Generated image 1" src="blob:https://chatgpt.com/ff6e435f-41f9"></button>
          <button data-testid="generated-image-preview" style="display:none"><img id="dup" alt="Generated image 1" src="blob:https://chatgpt.com/dup"></button></div></div>
      </div></div></main>`;
    expect(D.queryAll(CHATGPT.result_images).map((e) => e.id)).toContain('out');
    expect(D.queryAll(CHATGPT.user_images).map((e) => e.id)).toEqual(['ref']);
    // 后台标签页里正图量不出尺寸，所以不按显不显示筛；带上 alt 供 Flow 按 alt+尺寸去重副本
    expect(D.images(CHATGPT.result_images).map((i) => i.alt)).toEqual(['Generated image 1', 'Generated image 1']);
    expect(CHATGPT.image_src_hint.some((h) => 'blob:https://chatgpt.com/ff6e435f-41f9'.includes(h))).toBe(true);
    expect(D.visible(CHATGPT.busy_marker)).toBe(true);
    document.querySelector('[data-talvt-turn-state]').setAttribute('data-talvt-turn-state', 'complete');
    expect(D.visible(CHATGPT.busy_marker)).toBe(false);
  });

  it('上传框挑输入框旁边的（侧栏的不要）；数输入框那一块里的缩略图；回复文字去掉用户那条消息', () => {
    document.body.innerHTML = `
      <nav><input id="side" type="file" accept="image/*"></nav>
      <main>
        <div data-talvt-turn-state="complete"><div data-user-message-bubble="">画一张三视图 rate limit</div>
          <p>Please upload the reference image again</p></div>
        <div id="composer"><div id="thumbs"><img src="blob:x"></div>
          <div id="prompt-textarea" contenteditable="true"></div>
          <input id="near" type="file" accept="image/*" multiple></div>
      </main>`;
    expect(D.pickFileInput(CHATGPT.file_input, CHATGPT.prompt_box).id).toBe('near');
    expect(D.pickFileInput(CHATGPT.file_input).id).toBe('side');
    expect(D.fileInputs(CHATGPT.file_input, CHATGPT.prompt_box)).toEqual({ total: 2, near: 1 });
    expect(D.composerImages(CHATGPT.prompt_box)).toBe(1);
    expect(D.text(CHATGPT.assistant_turn, CHATGPT.reply_exclude)).toBe('Please upload the reference image again');
  });

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

  it('replyText（网页文本模型）：取最后一条回复的全文，保留换行和代码块原文，去掉用户消息和按钮；count 是命中数', () => {
    document.body.innerHTML = `
      <main><div data-talvt-turn-state="complete">
        <div data-user-message-bubble="">写个梗概</div>
        <h4 data-conversation-role="assistant">ChatGPT said:</h4>
        <div class="markdown"><p>好的</p><pre><div>json<button>Copy code</button></div><code>{
  "text": "第一集"
}</code></pre></div>
      </div></main>`;
    const r = D.replyText(CHATGPT.text_reply, CHATGPT.text_exclude);
    expect(r.count).toBeGreaterThan(0);
    expect(r.text).toContain('{\n  "text": "第一集"\n}');
    expect(r.text).not.toContain('Copy code');
    expect(r.text).not.toContain('写个梗概');
    expect(D.replyText('.nothing', '')).toEqual({ text: '', count: 0 });
  });

  it('Gemini / Claude 回复正文的选择器', () => {
    document.body.innerHTML = `
      <model-response><message-content>旧的回复</message-content></model-response>
      <model-response><model-thoughts>想一想</model-thoughts><message-content>{"text": "新的"}</message-content></model-response>`;
    expect(D.replyText(GEMINI.text_reply, GEMINI.text_exclude).text).toBe('{"text": "新的"}');
    document.body.innerHTML = `
      <div data-is-streaming="true"><div class="font-claude-response">{"text": "写到一半</div></div>
      <button aria-label="Stop response">停</button>
      <div class="ProseMirror" contenteditable="true"></div>`;
    expect(D.replyText(CLAUDE.text_reply, CLAUDE.text_exclude).text).toBe('{"text": "写到一半');
    expect(D.visible(CLAUDE.busy_marker)).toBe(true);
    expect(D.visible(CLAUDE.stop_button)).toBe(true);
    expect(D.visible(CLAUDE.prompt_box)).toBe(true);
  });
});
