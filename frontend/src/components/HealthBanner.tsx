import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useHealth } from '../api/queries';

const DISMISS_KEY = 'ai-studio.healthBanner.dismissed';

function readDismissed(): string | null {
  try {
    return sessionStorage.getItem(DISMISS_KEY);
  } catch {
    return null;
  }
}

function writeDismissed(key: string) {
  try {
    sessionStorage.setItem(DISMISS_KEY, key);
  } catch {
    // 存不了（隐私模式等）：只在这次页面里记住
  }
}

/**
 * ComfyUI 或浏览器扩展不可用时，在任务条下面挂一条黄色横幅（一行）：出图出视频前就知道会失败。
 * 具体原因（连不上哪个地址、超时还是拒绝连接、原始错误）放在悬停提示里。
 * 关掉后这个浏览器标签页里同样的问题不再提示（记在 sessionStorage）；换了问题（比如变成扩展断开）会重新出现。
 */
export function HealthBanner() {
  const { data } = useHealth();
  const [dismissed, setDismissed] = useState<string | null>(readDismissed);
  if (!data) return null;
  const kinds: string[] = [];
  const parts: string[] = [];
  const details: string[] = [];
  if (!data.comfyui.ok) {
    kinds.push('comfyui');
    parts.push('ComfyUI 没连上，本机出图、出视频会失败');
    details.push(`ComfyUI：${[data.comfyui.message, data.comfyui.detail].filter(Boolean).join('；') || '连不上'}`);
  }
  if (!data.browser_ext.ok) {
    kinds.push('browser_ext');
    parts.push('浏览器扩展没连上，网页出图会失败');
    details.push(`浏览器扩展：${[data.browser_ext.message, data.browser_ext.detail].filter(Boolean).join('；') || '连不上'}`);
  }
  if (kinds.length === 0) return null;
  const key = kinds.join(',');
  if (dismissed === key) return null;
  return (
    <div className="health-banner" role="alert" data-testid="health-banner">
      <span className="health-msgs" title={details.join('\n')}>
        {parts.join(' · ')}
      </span>
      <Link to="/settings">去设置</Link>
      <button
        type="button"
        className="ghost icon-btn"
        aria-label="关闭提醒"
        onClick={() => {
          writeDismissed(key);
          setDismissed(key);
        }}
      >
        ×
      </button>
    </div>
  );
}
