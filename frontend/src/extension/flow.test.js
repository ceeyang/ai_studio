// 扩展的出图流程（browser-extension/lib/flow.js）：和 backend/tests/test_browser_flow.py 同样的场景。
import { describe, expect, it } from 'vitest';
import { Canceled, FlowError, generate } from '../../../browser-extension/lib/flow.js';
import { CHATGPT, GEMINI } from '../../../browser-extension/sites/index.js';
import { FakePage, PNG_B64 } from './fakePage.js';

const GPT_PARTIAL = 'https://chatgpt.com/backend-api/estuary/content?id=file_partial';
const GPT_FINAL = 'https://chatgpt.com/backend-api/estuary/content?id=file_final';
const GEM_FINAL = 'https://lh3.googleusercontent.com/gg/final=s1024';
const REF = { name: 'chen.png', mime: 'image/png', b64: PNG_B64 };

async function run(page, { spec = CHATGPT, refs = [], timeout = 300, canceled = () => false, ...job } = {}) {
  const logs = [];
  const prog = [];
  const out = await generate(page, spec, { prompt: 'PROMPT', refs, timeout, ...job }, {
    isCanceled: canceled, log: (m) => logs.push(m), progress: (v, m) => prog.push([v, m]), clock: page.clock, poll: 1,
  });
  return { out, logs, prog };
}

async function fails(promise) {
  try {
    await promise;
  } catch (e) {
    return e;
  }
  throw new Error('没有失败');
}

const gptScript = (spec = CHATGPT) => [
  [1, (p) => p.show(spec.stop_button)],
  [20, (p) => p.addResult(GPT_PARTIAL)],
  [60, (p) => p.finish(GPT_FINAL)],
];

describe('extension flow', () => {
  it('ChatGPT：新对话、输提示词、等停止按钮消失且地址稳定、取原图', async () => {
    const page = new FakePage(CHATGPT).afterSubmit(gptScript());
    const { out, logs, prog } = await run(page);
    expect(out).toEqual([{ src: GPT_FINAL, mime: 'image/png', b64: PNG_B64, via: 'page' }]);
    expect(page.calls).toContainEqual(['goto', CHATGPT.new_chat_url]);
    expect(page.calls).toContainEqual(['type', 'PROMPT']);
    expect(page.calls).not.toContainEqual(['fetch', GPT_PARTIAL]); // 半成品不算
    expect(page.t).toBeGreaterThanOrEqual(60 + CHATGPT.settle_seconds);
    expect(prog[0]).toEqual([0, 100]);
    expect(prog.at(-1)).toEqual([100, 100]);
    expect(logs.some((m) => m.includes('已提交'))).toBe(true);
  });

  it('上传参考图走 file input，网站回显的参考图不算结果（5 秒回显规则）', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([
      [1, (p) => p.show(CHATGPT.stop_button)],
      [2, (p) => p.addResult(GPT_PARTIAL, 1024, 1024)], // 回显的参考图，地址格式和结果图一样
      [30, (p) => { p.hide(CHATGPT.stop_button); p.addResult(GPT_FINAL); }],
    ]);
    const { out, logs } = await run(page, { refs: [REF, { ...REF, name: 'yu.png' }] });
    expect(page.calls).toContainEqual(['setFiles', ['chen.png', 'yu.png']]);
    expect(out.map((o) => o.src)).toEqual([GPT_FINAL]);
    expect(logs.some((m) => m.includes('上传 2 张参考图'))).toBe(true);
  });

  it('没有 file input：点开菜单再找，还没有就粘贴图片', async () => {
    const page = new FakePage(GEMINI, { hasFileInput: false, pasteWorks: true }).afterSubmit([
      [1, (p) => p.show(GEMINI.stop_button)],
      [20, (p) => p.finish(GEM_FINAL)],
    ]);
    page.blobs.set('https://lh3.googleusercontent.com/gg/final=s0', PNG_B64);
    const { out } = await run(page, { spec: GEMINI, refs: [REF] });
    expect(page.calls).toContainEqual(['click', GEMINI.upload_buttons[0]]);
    expect(page.calls).not.toContainEqual(['click', GEMINI.upload_buttons[1]]); // 会弹文件框的不点
    expect(page.calls).toContainEqual(['pasteFiles', ['chen.png']]);
    // Gemini 先试原尺寸地址 =s0
    expect(out[0].src).toBe('https://lh3.googleusercontent.com/gg/final=s0');
  });

  it('Gemini 原尺寸地址取不到就取页面里那张', async () => {
    const page = new FakePage(GEMINI).afterSubmit([[1, (p) => p.show(GEMINI.stop_button)], [20, (p) => p.finish(GEM_FINAL)]]);
    const { out } = await run(page, { spec: GEMINI });
    expect(page.calls).toContainEqual(['fetch', 'https://lh3.googleusercontent.com/gg/final=s0']);
    expect(out[0].src).toBe(GEM_FINAL);
  });

  it('粘贴和拖放都没反应：上传失败，指明要改哪个文件', async () => {
    const page = new FakePage(CHATGPT, { hasFileInput: false });
    const e = await fails(run(page, { refs: [REF] }));
    expect(e).toBeInstanceOf(FlowError);
    expect(e.kind).toBe('page');
    expect(e.message).toContain('browser-extension/sites/chatgpt.js');
    expect(page.calls).toContainEqual(['dropFiles', ['chen.png']]);
  });

  it('登录墙 → not_logged_in，提示在这个浏览器里登录', async () => {
    const e = await fails(run(new FakePage(CHATGPT, { loggedIn: false })));
    expect(e.kind).toBe('not_logged_in');
    expect(e.message).toContain('在你平时用的这个浏览器里打开 chatgpt.com 登录');
  });

  it('被跳到登录域名 → not_logged_in', async () => {
    const page = new FakePage(GEMINI, { promptBox: false });
    page.goto = async (url) => { page.urlNow = 'https://accounts.google.com/v3/signin?continue=' + url; };
    const e = await fails(run(page, { spec: GEMINI }));
    expect(e.kind).toBe('not_logged_in');
  });

  it('没读到 cookie 但页面没有登录按钮：5 秒后按已登录继续', async () => {
    const page = new FakePage(CHATGPT).afterSubmit(gptScript());
    page.cookies = [];
    const { logs } = await run(page);
    expect(logs.some((m) => m.includes('没读到登录 cookie'))).toBe(true);
  });

  it('输入框一直不出现 → page 错误，指明 prompt_box', async () => {
    const e = await fails(run(new FakePage(CHATGPT, { promptBox: false })));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('prompt_box');
  });

  it('限额', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[3, (p) => { p.reply = "You've hit the plus plan limit for image generations"; }]]);
    const e = await fails(run(page));
    expect(e.kind).toBe('quota');
  });

  it('提示条里的限额（Gemini 的 snack bar）', async () => {
    const page = new FakePage(GEMINI).afterSubmit([[3, (p) => { p.alert = '你已达到今日图片生成上限，请明天再试'; }]]);
    const e = await fails(run(page, { spec: GEMINI }));
    expect(e.kind).toBe('quota');
  });

  it('内容审核拒绝', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[3, (p) => { p.reply = "I can't create that image because it violates our content policy."; }]]);
    const e = await fails(run(page));
    expect(e.kind).toBe('refused');
  });

  it('只回文字不出图', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[3, (p) => { p.reply = '你想要什么风格？'; }]]);
    const e = await fails(run(page));
    expect(e.kind).toBe('no_image');
    expect(e.message).toContain('你想要什么风格');
  });

  it('超时会点停止', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[1, (p) => p.show(CHATGPT.stop_button)]]);
    const e = await fails(run(page, { timeout: 30 }));
    expect(e.kind).toBe('timeout');
    expect(page.calls).toContainEqual(['click', CHATGPT.stop_button]);
  });

  it('取消：点停止并抛 Canceled', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[1, (p) => p.show(CHATGPT.stop_button)]]);
    const e = await fails(run(page, { canceled: () => page.t > 10 }));
    expect(e).toBeInstanceOf(Canceled);
    expect(page.calls).toContainEqual(['click', CHATGPT.stop_button]);
  });

  it('reuseConversation：已经在网站上时不跳新对话；新开的标签页也不再跳一次', async () => {
    const page = new FakePage(CHATGPT).afterSubmit(gptScript());
    page.urlNow = 'https://chatgpt.com/c/abc';
    await run(page, { reuseConversation: true });
    expect(page.calls.some((c) => c[0] === 'goto')).toBe(false);
    const fresh = new FakePage(CHATGPT).afterSubmit(gptScript());
    fresh.urlNow = 'https://chatgpt.com/';
    await run(fresh, { fresh: true });
    expect(fresh.calls.some((c) => c[0] === 'goto')).toBe(false);
  });

  it('取回来的不是图片：报错', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([[1, (p) => p.show(CHATGPT.stop_button)], [20, (p) => p.finish(GPT_FINAL)]]);
    page.blobs.set(GPT_FINAL, btoa('<html>login</html>'));
    page.addResult = () => {};
    page.finish = function finish(src) {
      this.hide(CHATGPT.stop_button);
      this.results = [{ src, w: 1024, h: 1536, complete: true }];
    };
    const e = await fails(run(page));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('不是图片');
  });
});
