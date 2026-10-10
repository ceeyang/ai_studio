import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom';

/** 根路由的出错页：页面渲染抛错或路由 404 时代替 react-router 自带的英文错误页。 */
export function RouteError() {
  const error = useRouteError();
  const detail = isRouteErrorResponse(error)
    ? error.status === 404
      ? '找不到这个页面。'
      : `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error ?? '');
  return (
    <div className="route-error" role="alert">
      <h2>页面出错了</h2>
      <p className="muted">这一页显示时出了问题，已经做的内容都存在服务器上，不会丢。可以回到项目中心，或者重新加载试试。</p>
      {detail && <pre className="error">{detail}</pre>}
      <div className="row">
        <Link className="btn primary" to="/">
          回到项目中心
        </Link>
        <button type="button" onClick={() => window.location.reload()}>
          重新加载
        </button>
      </div>
    </div>
  );
}
