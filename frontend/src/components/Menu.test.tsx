import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Menu } from './Menu';

function setup() {
  const up = vi.fn();
  const split = vi.fn();
  const del = vi.fn();
  render(
    <div>
      <button type="button">外面</button>
      <Menu
        label="更多操作 S01"
        items={[
          { label: '上移', onSelect: up, disabled: true },
          { label: '拆分', onSelect: split },
          { label: '删除', onSelect: del, danger: true },
        ]}
      />
    </div>,
  );
  return { up, split, del, trigger: screen.getByRole('button', { name: '更多操作 S01' }) };
}

describe('Menu', () => {
  it('触发按钮标明有弹出菜单；打开后焦点落在第一个能点的项上', async () => {
    const { trigger } = setup();
    expect(trigger.getAttribute('aria-haspopup')).toBe('menu');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('menu')).toBeNull();
    await userEvent.setup().click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('menu', { name: '更多操作 S01' })).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '拆分' }));
    expect((screen.getByRole('menuitem', { name: '上移' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('方向键在能点的项之间循环，Home/End 到头尾', async () => {
    const { trigger } = setup();
    const user = userEvent.setup();
    await user.click(trigger);
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
    await user.keyboard('{ArrowDown}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '拆分' }));
    await user.keyboard('{ArrowUp}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
    await user.keyboard('{Home}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '拆分' }));
    await user.keyboard('{End}');
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '删除' }));
  });

  it('Esc 关闭并把焦点还给触发按钮；点外面关闭', async () => {
    const { trigger } = setup();
    const user = userEvent.setup();
    await user.click(trigger);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(trigger);
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: '外面' }));
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('键盘打开：在触发按钮上按向下键', async () => {
    const { trigger } = setup();
    const user = userEvent.setup();
    trigger.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menu')).toBeTruthy();
    expect(document.activeElement).toBe(screen.getByRole('menuitem', { name: '拆分' }));
  });

  it('点菜单项执行并关闭；禁用项点了没反应', async () => {
    const { trigger, up, del } = setup();
    const user = userEvent.setup();
    await user.click(trigger);
    await user.click(screen.getByRole('menuitem', { name: '上移' }));
    expect(up).not.toHaveBeenCalled();
    await user.click(screen.getByRole('menuitem', { name: '删除' }));
    expect(del).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});
