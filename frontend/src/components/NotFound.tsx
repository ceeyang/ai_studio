import { Link } from 'react-router-dom';

export function NotFound() {
  return (
    <section className="panel">
      <h2>页面不存在</h2>
      <Link to="/">回到项目中心</Link>
    </section>
  );
}
