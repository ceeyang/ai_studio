import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { Outlet, RouterProvider, createMemoryRouter, useLocation } from 'react-router-dom';
import { ConfirmProvider } from '../components/ConfirmDialog';
import { LeaveGuard } from '../components/EditableFields';
import { MediaViewerHost } from '../components/MediaViewer';

export function newQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

interface RenderOptions {
  route?: string;
  path?: string;
  qc?: QueryClient;
}

/**
 * 和 App 一样用数据路由（createMemoryRouter + RouterProvider）渲染：根路由里有 ConfirmProvider（正文可以放链接）
 * 和未保存修改的离开拦截；ui 挂在 path 上，当前地址显示在 data-testid="location"。
 */
export function renderAt(ui: ReactElement, { route = '/', path = '*', qc = newQueryClient() }: RenderOptions = {}) {
  const router = createMemoryRouter(
    [
      {
        element: (
          <ConfirmProvider>
            <Outlet />
            <LocationProbe />
            <LeaveGuard />
            <MediaViewerHost />
          </ConfirmProvider>
        ),
        children: [{ path, element: ui }, ...(path !== '*' ? [{ path: '*', element: null }] : [])],
      },
    ],
    { initialEntries: [route] },
  );
  const result = render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { qc, router, ...result };
}
