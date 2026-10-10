import { render, screen } from '@testing-library/react';
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

  it('没改过时任务更新了对白就直接换成新的', () => {
    const { rerender } = render(<DialogueEditor dialogue={LINES} onSave={vi.fn()} />);
    const next = [{ ...LINES[0], km: 'ប្ដូរ?' }, LINES[1]];
    rerender(<DialogueEditor dialogue={next} onSave={vi.fn()} />);
    expect((screen.getByLabelText('第 1 句高棉语') as HTMLTextAreaElement).value).toBe('ប្ដូរ?');
    expect(screen.queryByText(/对白已被任务更新/)).toBeNull();
  });

  it('正在改时任务更新了对白：按句按字段合并，改过的不覆盖，提示可载入最新', async () => {
    const onDirtyChange = vi.fn();
    const { rerender } = render(<DialogueEditor dialogue={LINES} onSave={vi.fn()} onDirtyChange={onDirtyChange} />);
    const user = userEvent.setup();
    const zh2 = screen.getByLabelText('第 2 句中文');
    await user.clear(zh2);
    await user.type(zh2, '我先看看。');
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    // 任务（比如翻译）改了第 1 句高棉语和第 2 句中文
    const next = [{ ...LINES[0], km: 'ប្ដូរ?' }, { ...LINES[1], zh: '让我先看看。', km: 'សុំមើលសិន។' }];
    rerender(<DialogueEditor dialogue={next} onSave={vi.fn()} onDirtyChange={onDirtyChange} />);
    expect((screen.getByLabelText('第 1 句高棉语') as HTMLTextAreaElement).value).toBe('ប្ដូរ?');
    expect((screen.getByLabelText('第 2 句高棉语') as HTMLTextAreaElement).value).toBe('សុំមើលសិន។');
    expect((screen.getByLabelText('第 2 句中文') as HTMLInputElement).value).toBe('我先看看。');
    expect(screen.getByText(/对白已被任务更新/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '载入最新' }));
    expect((screen.getByLabelText('第 2 句中文') as HTMLInputElement).value).toBe('让我先看看。');
    expect(screen.queryByText(/对白已被任务更新/)).toBeNull();
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });
});
