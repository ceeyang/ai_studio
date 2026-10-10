import { render, screen } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { RouteError } from './RouteError';

function Boom(): never {
  throw new Error('渲染炸了');
}

describe('RouteError', () => {
  it('页面渲染出错：中文说明、错误信息、回到项目中心、重新加载', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const router = createMemoryRouter([{ path: '/', element: <Boom />, errorElement: <RouteError /> }], { initialEntries: ['/'] });
    render(<RouterProvider router={router} />);
    expect(screen.getByRole('heading', { name: '页面出错了' })).toBeTruthy();
    expect(screen.getByText(/渲染炸了/)).toBeTruthy();
    expect(screen.getByRole('link', { name: '回到项目中心' }).getAttribute('href')).toBe('/');
    expect(screen.getByRole('button', { name: '重新加载' })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/Unexpected Application Error/);
  });

  it('不存在的路由（404）也是中文', () => {
    const router = createMemoryRouter([{ path: '/', element: <p>首页</p>, errorElement: <RouteError /> }], { initialEntries: ['/nope'] });
    render(<RouterProvider router={router} />);
    expect(screen.getByRole('heading', { name: '页面出错了' })).toBeTruthy();
    expect(screen.getByText(/找不到这个页面/)).toBeTruthy();
  });
});
