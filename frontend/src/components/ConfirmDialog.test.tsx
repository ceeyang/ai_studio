import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ConfirmProvider, useConfirm } from './ConfirmDialog';

function Probe({ onResult }: { onResult: (v: boolean) => void }) {
  const confirm = useConfirm();
  return (
    <button
      type="button"
      onClick={async () => onResult(await confirm({ title: '删除镜头 S01？', body: '候选会一起删掉', confirmText: '删除', danger: true }))}
    >
      删
    </button>
  );
}

describe('ConfirmDialog', () => {
  it('确认 → true；取消 → false；弹窗显示标题和正文', async () => {
    const user = userEvent.setup();
    const results: boolean[] = [];
    render(
      <ConfirmProvider>
        <Probe onResult={(v) => results.push(v)} />
      </ConfirmProvider>,
    );
    await user.click(screen.getByRole('button', { name: '删' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('删除镜头 S01？')).toBeTruthy();
    expect(screen.getByText('候选会一起删掉')).toBeTruthy();
    const ok = screen.getByRole('button', { name: '删除' });
    expect(ok.className).toContain('danger');
    await user.click(ok);
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(async () => {});
    expect(results).toEqual([true]);

    await user.click(screen.getByRole('button', { name: '删' }));
    await user.click(screen.getByRole('button', { name: '取消' }));
    await act(async () => {});
    expect(results).toEqual([true, false]);
  });

  it('Esc 等于取消', async () => {
    const user = userEvent.setup();
    const results: boolean[] = [];
    render(
      <ConfirmProvider>
        <Probe onResult={(v) => results.push(v)} />
      </ConfirmProvider>,
    );
    await user.click(screen.getByRole('button', { name: '删' }));
    await user.keyboard('{Escape}');
    await act(async () => {});
    expect(results).toEqual([false]);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('没有 confirmText 时按钮叫「确定」', async () => {
    const user = userEvent.setup();
    function P() {
      const confirm = useConfirm();
      return <button type="button" onClick={() => void confirm({ title: 't', body: 'b' })}>开</button>;
    }
    render(
      <ConfirmProvider>
        <P />
      </ConfirmProvider>,
    );
    await user.click(screen.getByRole('button', { name: '开' }));
    expect(screen.getByRole('button', { name: '确定' }).className).toContain('primary');
  });
});
