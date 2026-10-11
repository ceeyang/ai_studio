// 扩展出图流程的续接会话（v0.3 起，browser-extension/lib/flow.js）：和 backend/tests/test_web_conversations.py 同样的场景。
import { describe, expect, it } from 'vitest';
import { conversationId, generate } from '../../../browser-extension/lib/flow.js';
import { FEATURES } from '../../../browser-extension/lib/protocol.js';
import { CHATGPT, CLAUDE, GEMINI } from '../../../browser-extension/sites/index.js';
import { FakePage } from './fakePage.js';

const CONV = 'https://chatgpt.com/c/68e9a1b2-0001-8000-aaaa-000000000001';
const OLD = 'blob:https://chatgpt.com/old-1';
const NEW = 'blob:https://chatgpt.com/new-1';

/** 一条已有的对话：页面上有上一轮的图（alt 和尺寸都和新图一样）和上一轮的回复。 */
function oldConversation() {
  const page = new FakePage(CHATGPT);
  page.addResult(OLD, 1024, 1536, undefined, 'Generated image 1');
  page.reply = 'Here is the image you asked for.';
  page.show(CHATGPT.assistant_turn);
  return page.afterSubmit([
    [1, (p) => p.show(CHATGPT.stop_button)],
    [40, (p) => {
      p.hide(CHATGPT.stop_button);
      p.addResult(NEW, 1024, 1536, undefined, 'Generated image 1');
      p.reply = 'Here is the new image.';
    }],
  ]);
}

async function run(page, conversationUrl) {
  const logs = [];
  let conv = null;
  const out = await generate(page, CHATGPT, { prompt: 'PROMPT', refs: [], timeout: 300, conversationUrl }, {
    log: (m) => logs.push(m), clock: page.clock, poll: 1, onConversation: (c) => { conv = c; },
  });
  return { out, logs, conv };
}

describe('extension flow：续接会话', () => {
  it('扩展报 conversation 能力', () => {
    expect(FEATURES).toContain('conversation');
    expect(FEATURES).toContain('rpc');
  });

  it('认对话地址', () => {
    expect(conversationId(CHATGPT, CONV)).toBe('68e9a1b2-0001-8000-aaaa-000000000001');
    expect(conversationId(CHATGPT, 'https://chatgpt.com/')).toBe('');
    expect(conversationId(CHATGPT, 'https://evil.example/c/68e9a1b2-0001')).toBe('');
    expect(conversationId(GEMINI, 'https://gemini.google.com/u/1/app/1a2b3c4d5e6f')).toBe('1a2b3c4d5e6f');
    expect(conversationId(GEMINI, 'https://gemini.google.com/app')).toBe('');
    expect(conversationId(CLAUDE, 'https://claude.ai/chat/0b1c2d3e-aaaa-bbbb')).toBe('0b1c2d3e-aaaa-bbbb');
    expect(conversationId(CHATGPT, '')).toBe('');
  });

  it('续接：跳到那条对话、只取这次新出的图（旧图 alt 和尺寸一样也不算），报回地址', async () => {
    const page = oldConversation();
    const { out, logs, conv } = await run(page, CONV);
    expect(out.map((o) => o.src)).toEqual([NEW]);
    expect(page.calls).toContainEqual(['goto', CONV]);
    expect(page.calls).not.toContainEqual(['goto', CHATGPT.new_chat_url]);
    expect(page.calls).not.toContainEqual(['fetch', OLD]);
    expect(conv).toEqual({ url: CONV, continued: true, note: '' });
    expect(logs.some((m) => m.includes('打开之前的会话'))).toBe(true);
  });

  it('续接：上一轮的回复还在页面上，不当成「回复了文字没出图」', async () => {
    const page = oldConversation().afterSubmit([
      [1, (p) => p.show(CHATGPT.stop_button)],
      [2, (p) => p.hide(CHATGPT.stop_button)],
      [60, (p) => p.addResult(NEW, 1024, 1536)],
    ]);
    const { out } = await run(page, CONV);
    expect(out.map((o) => o.src)).toEqual([NEW]);
  });

  it('对话被删了（跳回首页）：新开对话，原因报回去', async () => {
    const page = oldConversation();
    const goto = page.goto.bind(page);
    page.goto = async (url) => {
      await goto(url);
      if (url === CONV) page.urlNow = 'https://chatgpt.com/';
    };
    const { conv, logs } = await run(page, CONV);
    expect(page.calls).toContainEqual(['goto', CHATGPT.new_chat_url]);
    expect(conv.continued).toBe(false);
    expect(conv.note).toContain('会话打不开');
    expect(logs.some((m) => m.includes('新开对话'))).toBe(true);
  });

  it('对话是空的（看不到之前的回复）：新开对话', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([
      [1, (p) => p.show(CHATGPT.stop_button)],
      [30, (p) => p.finish(NEW)],
    ]);
    const { out, conv } = await run(page, CONV);
    expect(conv.continued).toBe(false);
    expect(conv.note).toContain('看不到之前的回复');
    expect(out.map((o) => o.src)).toEqual([NEW]);
  });

  it('没给会话地址：照旧新开，onConversation 报 continued=false', async () => {
    const page = new FakePage(CHATGPT).afterSubmit([
      [1, (p) => p.show(CHATGPT.stop_button)],
      [30, (p) => p.finish(NEW)],
    ]);
    const { conv } = await run(page, '');
    expect(page.calls).toContainEqual(['goto', CHATGPT.new_chat_url]);
    expect(conv).toMatchObject({ continued: false, note: '' });
  });
});
