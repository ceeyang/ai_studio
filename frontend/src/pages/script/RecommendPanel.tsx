// 按剧本推荐角色：LLM 读这一集，列出剧本需要、角色表里还没有的人物，一键添加。
// 任务号记在地址的 ?recommend=<id> 里：刷新或离开再回来还能看到这次的结果。
import { Link, useSearchParams } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useCreateCharacter, useJob, useRecommendCharacters } from '../../api/queries';
import type { Character, RecommendedCharacter } from '../../api/types';
import { nextActionProps } from '../../components/EpisodeHeader';

export function RecommendPanel({
  slug,
  episodeId,
  existing,
  isNext = false,
}: {
  slug: string;
  episodeId: number;
  existing: Character[];
  /** 还没有角色、页头的下一步是「添加角色」：推荐按钮是本页唯一的主按钮 */
  isNext?: boolean;
}) {
  const recommend = useRecommendCharacters(episodeId);
  const create = useCreateCharacter(slug);
  const [params, setParams] = useSearchParams();
  const fromUrl = Number(params.get('recommend'));
  const jobId = Number.isInteger(fromUrl) && fromUrl > 0 ? fromUrl : null;
  const setJobId = (id: number) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set('recommend', String(id));
        return next;
      },
      { replace: true },
    );
  const job = useJob(jobId);
  const status = job.data?.status;
  const running = recommend.isPending || status === 'queued' || status === 'running';
  const names = new Set(existing.map((c) => c.name));
  const found = (job.data?.result?.characters as RecommendedCharacter[] | undefined) ?? [];
  const todo = found.filter((r) => !names.has(r.name));

  return (
    <div className="recommend">
      <div className="row">
        <button
          type="button"
          {...nextActionProps(isNext)}
          disabled={running}
          onClick={() => recommend.mutate(3, { onSuccess: (r) => setJobId(r.job_ids[0]) })}
        >
          {running ? '按剧本推荐中…' : '按剧本推荐角色'}
        </button>
        {status === 'done' && found.length === 0 && <span className="muted">剧本里的角色都已经在表里了。</span>}
        {status === 'done' && found.length > 0 && todo.length === 0 && <span className="muted">推荐的角色都已添加。</span>}
        {(status === 'failed' || status === 'canceled') && (
          <span className="error">
            推荐失败：{job.data?.error ?? status} · <Link to={`/jobs?job=${jobId}`}>查看任务</Link>
          </span>
        )}
        {status === 'waiting_input' && (
          <span className="notice">
            在等人工输入 · <Link to={`/jobs?job=${jobId}`}>去填写</Link>
          </span>
        )}
      </div>
      {recommend.error && <p className="error">{errorText(recommend.error)}</p>}
      {todo.length > 0 && (
        <div className="recommend-list">
          {todo.map((r) => (
            <div key={r.name} className="option-card" data-testid={`recommend-${r.name}`}>
              <div className="option-head">
                <strong>{r.name}</strong>
                <button
                  type="button"
                  className="soft"
                  disabled={create.isPending}
                  onClick={() =>
                    create.mutate({
                      name: r.name,
                      description: r.description,
                      visual_prompt: r.visual_prompt,
                      core_features: r.core_features,
                    })
                  }
                >
                  添加角色
                </button>
              </div>
              {r.description && <p className="char-desc">{r.description}</p>}
              {r.core_features && <p className="option-features">{r.core_features}</p>}
              <p className="option-prompt">{r.visual_prompt}</p>
            </div>
          ))}
        </div>
      )}
      {create.error && <p className="error">{errorText(create.error)}</p>}
    </div>
  );
}
