import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { NotFound } from './components/NotFound';
import { EventsProvider } from './ws/EventsContext';
import { SettingsPage } from './pages/SettingsPage';
import { JobsPage } from './pages/JobsPage';
import { DirectorPage } from './pages/DirectorPage';
import { ScriptPage } from './pages/ScriptPage';
import { ProjectsPage } from './pages/ProjectsPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: false } },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              {/* 页面路由 */}
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/jobs" element={<JobsPage />} />
              <Route path="/projects/:slug/episodes/:episodeId/director" element={<DirectorPage />} />
              <Route path="/projects/:slug/episodes/:episodeId/script" element={<ScriptPage />} />
              <Route path="/" element={<ProjectsPage />} />
              <Route path="/projects/:slug" element={<ProjectsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </EventsProvider>
    </QueryClientProvider>
  );
}
