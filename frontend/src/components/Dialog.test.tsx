import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Dialog } from './Dialog';

function Host({ guard, busy, onClose }: { guard?: () => boolean; busy?: boolean; onClose: () => void }) {
  return (
    <div>
      <button type="button">外面的按钮</button>
      <Dialog title="编辑" onClose={onClose} guard={guard} busy={busy}>
        <input aria-label="名字" />
        <button type="button">保存</button>
      </Dialog>
    </div>
  );
}

describe('Dialog', () => {
  it('打开时聚焦第一个输入框；Tab 在弹窗里循环，不跑到外面', async () => {
    const user = userEvent.setup();
    render(<Host onClose={() => {}} />);
    const input = screen.getByLabelText('名字');
    expect(document.activeElement).toBe(input);
    await user.tab(); // 保存
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '保存' }));
    await user.tab(); // 回到第一个可聚焦元素（关闭按钮）
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '关闭' }));
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '保存' }));
  });

  it('关闭后焦点还给打开它的元素', async () => {
    const user = userEvent.setup();
    function Opener() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>打开</button>
          {open && (
            <Dialog title="t" onClose={() => setOpen(false)}>
              <input aria-label="x" />
            </Dialog>
          )}
        </>
      );
    }
    render(<Opener />);
    const opener = screen.getByRole('button', { name: '打开' });
    await user.click(opener);
    expect(document.activeElement).toBe(screen.getByLabelText('x'));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('guard 返回 true 时，Esc 先问「放弃修改？」：继续编辑留在弹窗，放弃才关', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<Host guard={() => true} onClose={onClose} />);
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('放弃修改？')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(screen.queryByText('放弃修改？')).toBeNull();
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(screen.getByText('放弃修改？')).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '放弃' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('guard 返回 false 时直接关；点遮罩也走同样逻辑', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = render(<Host guard={() => false} onClose={onClose} />);
    await user.pointer({ keys: '[MouseLeft]', target: container.querySelector('.modal-scrim')! });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('busy 时 Esc、遮罩、关闭按钮都不关', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const { container } = render(<Host busy onClose={onClose} />);
    await user.keyboard('{Escape}');
    await user.pointer({ keys: '[MouseLeft]', target: container.querySelector('.modal-scrim')! });
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(onClose).not.toHaveBeenCalled();
  });

  it('叠在上面的弹窗按 Esc 只关自己', async () => {
    const user = userEvent.setup();
    const outer = vi.fn();
    const inner = vi.fn();
    render(
      <>
        <Dialog title="外层" onClose={outer}>
          <input aria-label="a" />
        </Dialog>
        <Dialog title="内层" onClose={inner}>
          <input aria-label="b" />
        </Dialog>
      </>,
    );
    await user.keyboard('{Escape}');
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });
});
