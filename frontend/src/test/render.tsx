import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

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

/** 在 MemoryRouter + QueryClientProvider 里渲染；ui 挂在 path 上，当前地址显示在 data-testid="location"。 */
export function renderAt(ui: ReactElement, { route = '/', path = '*', qc = newQueryClient() }: RenderOptions = {}) {
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
          {path !== '*' && <Route path="*" element={null} />}
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { qc, ...result };
}
