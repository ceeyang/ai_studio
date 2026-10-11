import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createBrowserRouter } from 'react-router-dom';
import { ConfirmProvider } from './components/ConfirmDialog';
import { LeaveGuard } from './components/EditableFields';
import { Layout } from './components/Layout';
import { MediaViewerHost } from './components/MediaViewer';
import { NotFound } from './components/NotFound';
import { RouteError } from './components/RouteError';
import { EventsProvider } from './ws/EventsContext';
import { SettingsPage } from './pages/SettingsPage';
import { JobsPage } from './pages/JobsPage';
import { episodeRoutes } from './pages/EpisodeWorkspace';
import { ProjectsPage } from './pages/ProjectsPage';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: false } },
});

/** 根路由：确认框放在路由里面（正文里可以放站内链接），未保存修改的离开拦截、看图弹窗全站只挂这一个。 */
function Root() {
  return (
    <ConfirmProvider>
      <Layout />
      <LeaveGuard />
      {/* 全局看图 / 看视频弹窗：页面里点图片、视频都在这里打开 */}
      <MediaViewerHost />
    </ConfirmProvider>
  );
}

// 数据路由（createBrowserRouter）：useBlocker 只在它下面可用，站内跳转才拦得住未保存的修改
const router = createBrowserRouter([
  {
    element: <Root />,
    // 渲染出错时显示中文出错页，而不是 react-router 自带的英文页
    errorElement: <RouteError />,
    children: [
      { path: '/settings', element: <SettingsPage /> },
      { path: '/jobs', element: <JobsPage /> },
      // 单集工作台；旧的 /script /cast /director /export 重定向过去（后端任务的 context.url 还是旧形式）
      ...episodeRoutes,
      { path: '/', element: <ProjectsPage /> },
      { path: '/projects/:slug', element: <ProjectsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <EventsProvider>
        <RouterProvider router={router} />
      </EventsProvider>
    </QueryClientProvider>
  );
}
