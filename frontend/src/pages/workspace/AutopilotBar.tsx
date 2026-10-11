// 工作台顶上的「点子出片」条（地址带 ?autopilot=1，从「从一个点子开始」向导过来）：
// 剧本 / 分镜在跑 → 说一声在写；分镜做好 → 「继续出片」一次排好定妆照 → 首帧 → 视频 → 导出；点过就只说排好了。
// 点没点过按集记在本机（localStorage；读写不了时只记在这次打开的页面里）。
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useAutopilot, useJobs } from '../../api/queries';
import type { AutopilotOut } from '../../api/types';

const key = (episodeId: number) => `studio.autopilot.${episodeId}`;

export function autopilotTriggered(episodeId: number): boolean {
  try {
    return window.localStorage.getItem(key(episodeId)) === '1';
  } catch {
    return false;
  }
}

function rememberTriggered(episodeId: number): void {
  try {
    window.localStorage.setItem(key(episodeId), '1');
  } catch {
    // 记不住也没关系：这次打开的页面里照样不再显示按钮
  }
}

export function AutopilotBar({ episodeId, hasShots, onDismiss }: { episodeId: number; hasShots: boolean; onDismiss: () => void }) {
  const run = useAutopilot(episodeId);
  // 剧本 / 分镜失败了（模型连不上、输出不合格）：别一直说「正在写」，把原因说出来
  const failedJobs = useJobs('failed', 50);
  const failed = hasShots
    ? undefined
    : (failedJobs.data ?? []).find(
        (j) => j.owner_type === 'episode' && j.owner_id === episodeId && (j.type === 'script' || j.type === 'storyboard'),
      );
  const [triggered, setTriggered] = useState(() => autopilotTriggered(episodeId));
  const [result, setResult] = useState<AutopilotOut | null>(null);

  const go = () =>
    run.mutate(undefined, {
      onSuccess: (r) => {
        rememberTriggered(episodeId);
        setTriggered(true);
        setResult(r);
      },
    });

  let text: string;
  if (triggered) text = '已排好：定妆照 → 首帧 → 视频 → 导出，可以在任务面板看进度。';
  else if (failed) text = `${failed.type === 'script' ? '剧本' : '分镜'}没写成：${failed.error || '任务失败'}。在下面对应的阶段点重试，写好后这里会出现「继续出片」。`;
  else if (!hasShots) text = '正在写剧本和分镜…';
  else text = '分镜做好了：看一下分镜和角色，没问题点「继续出片」（会出角色定妆照 → 首帧 → 视频 → 导出）。';

  return (
    <section className="autopilot-bar" aria-label="点子出片">
      <div className="autopilot-head">
        <h3>点子出片</h3>
        <p role="status">{text}</p>
        {triggered && (
          <Link className="btn sm" to="/jobs">
            任务面板
          </Link>
        )}
        {!triggered && hasShots && (
          <button type="button" className="primary" disabled={run.isPending} onClick={go}>
            {run.isPending ? '正在排…' : '继续出片'}
          </button>
        )}
        <button type="button" className="ghost sm" aria-label="关掉「点子出片」" title="关掉" onClick={onDismiss}>
          ×
        </button>
      </div>
      {/* 没能出定妆照的角色（后端写好的整句说明） */}
      {result && result.skipped.length > 0 && (
        <ul className="warn small">
          {result.skipped.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {result && result.warnings.length > 0 && <p className="warn small">{result.warnings.join(' ')}</p>}
      {run.error && <p className="error">{errorText(run.error)}</p>}
    </section>
  );
}
