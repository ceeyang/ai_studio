import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { EndCardPanel } from './EndCardPanel';

const CARD = {
  seconds: 3,
  background: '#0b1a33',
  lines: [{ text: 'កម្ពុជារថយន្តលេខរៀងទី1', font: 'Khmer UI', size: 92, bold: true }],
};

describe('EndCardPanel', () => {
  it('加一行、改字号后整张卡 PUT 回去', async () => {
    const calls = installFetch({
      'GET /api/projects/demo/end-card': CARD,
      'PUT /api/projects/demo/end-card': (c: { body: unknown }) => c.body,
    });
    renderAt(<EndCardPanel slug="demo" />);
    const user = userEvent.setup();
    expect(((await screen.findByLabelText('第 1 行文字')) as HTMLInputElement).value).toBe('កម្ពុជារថយន្តលេខរៀងទី1');
    await user.click(screen.getByRole('button', { name: '加一行' }));
    await user.type(screen.getByLabelText('第 2 行文字'), 'Telegram 0318891666');
    const size = screen.getByLabelText('第 2 行字号');
    await user.clear(size);
    await user.type(size, '60');
    await user.click(screen.getByLabelText('第 2 行粗体'));
    await user.click(screen.getByRole('button', { name: '保存片尾卡' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({
      ...CARD,
      lines: [...CARD.lines, { text: 'Telegram 0318891666', font: null, size: 60, bold: true }],
    });
  });

  it('没有片尾卡时从空白开始，删行后保存', async () => {
    const calls = installFetch({
      'GET /api/projects/demo/end-card': null,
      'PUT /api/projects/demo/end-card': (c: { body: unknown }) => c.body,
    });
    renderAt(<EndCardPanel slug="demo" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: '加一行' }));
    await user.click(screen.getByRole('button', { name: '加一行' }));
    await user.type(screen.getByLabelText('第 1 行文字'), '柬埔寨1号铭车');
    await user.click(screen.getByRole('button', { name: '删除第 2 行' }));
    await user.click(screen.getByRole('button', { name: '保存片尾卡' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT')).toBe(true));
    expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({
      seconds: 3,
      background: '#0b1a33',
      lines: [{ text: '柬埔寨1号铭车', font: null, size: 48, bold: false }],
    });
  });
});
