import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { DialogueLine } from '../../api/types';
import { renderAt } from '../../test/render';
import { DialogueEditor, fitNote } from './DialogueEditor';

const LINES: DialogueLine[] = [
  { speaker: '陈师傅', zh: '换？', start: 2, end: 2.6, km: 'ដូរ?', km_review: 'ai_draft' },
  { speaker: '陈师傅', zh: '先让我看看。', start: 2.8, end: 4.6 },
];

describe('DialogueEditor', () => {
  it('AI 译稿有待校对标记；改高棉语、勾已校对后整段对白一起保存', async () => {
    const onSave = vi.fn();
    renderAt(<DialogueEditor dialogue={LINES} onSave={onSave} />);
    expect(screen.getByText('AI 译稿·待母语校对')).toBeTruthy();
    const save = screen.getByRole('button', { name: '保存对白' }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    const user = userEvent.setup();
    const km2 = screen.getByLabelText('第 2 句高棉语');
    await user.type(km2, 'សុំមើលសិន។');
    await user.click(screen.getByLabelText('第 1 句母语已校对'));
    await user.click(save);
    expect(onSave).toHaveBeenCalledWith([
      { ...LINES[0], km_review: 'checked' },
      { ...LINES[1], km: 'សុំមើលសិន។' },
    ]);
  });

  it('按高棉语时长改写过的句子在中文旁边显示原中文和实测时长', () => {
    const fitted: DialogueLine = {
      speaker: '陈师傅', zh: '换？要换吗', zh_original: '换？', start: 1.6, end: 2.6, km: 'ប្ដូរ?',
      km_seconds: 0.42, zh_seconds: 0.44, fit_ratio: 1.048,
    };
    renderAt(<DialogueEditor dialogue={[fitted, LINES[1]]} onSave={vi.fn()} />);
    expect(screen.getByTestId('fit-note-1').textContent).toBe('原中文：换？ · 高棉语 0.42s / 中文 0.44s（1.05）');
    expect(screen.queryByTestId('fit-note-2')).toBeNull();
    expect(fitNote({ ...fitted, zh: '换？' })).toBe('原句未改 · 高棉语 0.42s / 中文 0.44s（1.05）');
  });

  it('改中文会提示视频过期；取消校对回到 AI 译稿', async () => {
    const onSave = vi.fn();
    renderAt(<DialogueEditor dialogue={[{ ...LINES[0], km_review: 'checked' }]} onSave={onSave} />);
    const user = userEvent.setup();
    const zh = screen.getByLabelText('第 1 句中文');
    await user.clear(zh);
    await user.type(zh, '换什么？');
    expect(screen.getByText(/改了中文台词/)).toBeTruthy();
    await user.click(screen.getByLabelText('第 1 句母语已校对'));
    await user.click(screen.getByRole('button', { name: '保存对白' }));
    expect(onSave).toHaveBeenCalledWith([{ ...LINES[0], zh: '换什么？', km_review: 'ai_draft' }]);
  });
});
