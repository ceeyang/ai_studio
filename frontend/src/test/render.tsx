import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { ConfirmProvider } from '../components/ConfirmDialog';

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
 * 在 MemoryRouter + QueryClientProvider + ConfirmProvider 里渲染（和 App 一样：确认框在路由里面，正文可以放链接）；
 * ui 挂在 path 上，当前地址显示在 data-testid="location"。
 */
export function renderAt(ui: ReactElement, { route = '/', path = '*', qc = newQueryClient() }: RenderOptions = {}) {
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <ConfirmProvider>
          <Routes>
            <Route path={path} element={ui} />
            {path !== '*' && <Route path="*" element={null} />}
          </Routes>
          <LocationProbe />
        </ConfirmProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, ...result };
}
