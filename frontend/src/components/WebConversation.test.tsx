// 网页出图的会话续接：「新开对话」开关、「在网页里打开这条会话」链接，只在网页引擎时出现。
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import type { BrowserConversations } from '../api/types';
import { installFetch } from '../test/fetch';
import { renderAt } from '../test/render';
import { WebConversation, isWebImageEngine } from './WebConversation';

const ENGINES = {
  image: [
    { name: 'zimage', family: 'zimage', runtime: 'comfyui', caps: {} },
    { name: 'gpt_image', family: 'chatgpt_web', runtime: 'browser', caps: {} },
    { name: 'gemini_image', family: 'gemini_web', runtime: 'browser', caps: {} },
  ],
  video: [],
};
const CONV: BrowserConversations = {
  reuse_enabled: true,
  supported: true,
  hint: null,
  max_turns: 20,
  items: [{ site: 'chatgpt', label: 'ChatGPT', url: 'https://chatgpt.com/c/abc', turns: 3, updated_at: '2026-10-11T10:00:00Z' }],
};
const URL_12 = 'GET /api/browser/conversations?scope_kind=character&scope_id=12';

function Harness({ engine }: { engine: string }) {
  const [v, setV] = useState(false);
  return <WebConversation scopeKind="character" scopeId={12} engine={engine} newConversation={v} onNewConversationChange={setV} />;
}

describe('isWebImageEngine', () => {
  it('按引擎列表的 runtime 判断；列表里没有时按名字认 gpt_image / gemini_image', () => {
    expect(isWebImageEngine('gpt_image', ENGINES.image as never)).toBe(true);
    expect(isWebImageEngine('zimage', ENGINES.image as never)).toBe(false);
    expect(isWebImageEngine('gemini_image')).toBe(true);
    expect(isWebImageEngine('h3_image')).toBe(false);
    expect(isWebImageEngine('')).toBe(false);
    expect(isWebImageEngine(undefined)).toBe(false);
  });
});

describe('WebConversation', () => {
  it('网页引擎：有「新开对话」开关和在新标签页打开的会话链接（写已几轮）', async () => {
    installFetch({ 'GET /api/engines': ENGINES, [URL_12]: CONV });
    renderAt(<Harness engine="gpt_image" />);
    const user = userEvent.setup();
    const link = await screen.findByRole('link', { name: '在网页里打开这条会话' });
    expect(link.getAttribute('href')).toBe('https://chatgpt.com/c/abc');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noreferrer');
    expect(screen.getByTestId('web-conversation').textContent).toContain('已 3 轮');
    const box = screen.getByRole('checkbox', { name: '新开对话' }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    await user.click(box);
    expect(box.checked).toBe(true);
  });

  it('两个网站都有会话：每条标明 ChatGPT / Gemini', async () => {
    installFetch({
      'GET /api/engines': ENGINES,
      [URL_12]: {
        ...CONV,
        items: [...CONV.items, { site: 'gemini', label: 'Gemini', url: 'https://gemini.google.com/app/x', turns: 1, updated_at: null }],
      },
    });
    renderAt(<Harness engine="gemini_image" />);
    const links = await screen.findAllByRole('link', { name: '在网页里打开这条会话' });
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://chatgpt.com/c/abc', 'https://gemini.google.com/app/x']);
    const text = screen.getByTestId('web-conversation').textContent ?? '';
    expect(text).toContain('ChatGPT：');
    expect(text).toContain('Gemini：');
  });

  it('还没有会话时写一句第一次会新开；扩展不支持时显示 hint', async () => {
    installFetch({ 'GET /api/engines': ENGINES, [URL_12]: { ...CONV, supported: false, hint: '重新加载扩展可复用会话', items: [] } });
    renderAt(<Harness engine="gpt_image" />);
    expect(await screen.findByText('还没有网页会话，第一次出图会新开。')).toBeTruthy();
    expect(screen.getByText('重新加载扩展可复用会话')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });

  it('设置里关了续接：写明每次新开，没有开关', async () => {
    installFetch({ 'GET /api/engines': ENGINES, [URL_12]: { ...CONV, reuse_enabled: false, items: [] } });
    renderAt(<Harness engine="gpt_image" />);
    expect((await screen.findByTestId('web-conv-reuse-off')).textContent).toContain('设置里已关闭续接，每次新开对话');
    expect(screen.queryByRole('checkbox')).toBeNull();
  });

  it('不是网页引擎：整块不显示，也不去问会话', async () => {
    const calls = installFetch({ 'GET /api/engines': ENGINES, [URL_12]: CONV });
    renderAt(<Harness engine="zimage" />);
    await waitFor(() => expect(calls.some((c) => c.url === '/api/engines')).toBe(true));
    expect(screen.queryByTestId('web-conversation')).toBeNull();
    expect(calls.some((c) => c.url.startsWith('/api/browser/conversations'))).toBe(false);
  });
});
