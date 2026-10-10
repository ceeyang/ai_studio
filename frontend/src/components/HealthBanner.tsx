import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useHealth } from '../api/queries';

/**
 * ComfyUI 或浏览器扩展不可用时，在任务条下面挂一条黄色横幅：出图出视频前就知道会失败。
 * 关掉后，同样的问题不再提示；问题内容变了（比如换成扩展断开）会重新出现。
 */
export function HealthBanner() {
  const { data } = useHealth();
  const [dismissed, setDismissed] = useState<string | null>(null);
  if (!data) return null;
  const problems: string[] = [];
  if (!data.comfyui.ok) problems.push(`ComfyUI 不可用：${data.comfyui.message || '连不上'}（本机出图、出视频会失败）`);
  if (!data.browser_ext.ok) problems.push(`浏览器扩展没连上：${data.browser_ext.message || '连不上'}（网页出图会失败）`);
  if (problems.length === 0) return null;
  const key = problems.join('\n');
  if (dismissed === key) return null;
  return (
    <div className="health-banner" role="alert" data-testid="health-banner">
      <div className="health-msgs">
        {problems.map((p) => (
          <span key={p}>{p}</span>
        ))}
      </div>
      <Link to="/settings">去设置</Link>
      <button type="button" className="ghost icon-btn" aria-label="关闭提醒" onClick={() => setDismissed(key)}>
        ×
      </button>
    </div>
  );
}
