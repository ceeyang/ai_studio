// 场景卡里的「场景图」：不出人物的环境参考图，出首帧时排在参考图最前面（固定同一场景各镜的布景）。
// 候选条（上传的 + AI 出的，单击看大图，「设为场景图」切换）、生成中 / 失败卡、出图（张数、引擎）和上传。
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useDrawLocationImages, useStepDefault, useUploadLocationImage, useUseLocationImage } from '../../api/queries';
import type { Location } from '../../api/types';
import { ImageEngineSelect } from '../../components/ImageEngineSelect';
import { Lightbox } from '../../components/Lightbox';
import { engineLabel } from '../../format';
import { useEvents } from '../../ws/EventsContext';

/** 场景有没有能用来出场景图的英文画面提示词（后端没有时直接拒绝） */
export function hasEnglishPrompt(loc: Location): boolean {
  const vp = loc.visual_prompt.trim();
  return vp !== '' && !/[⺀-鿿]/.test(vp);
}

export function LocationImages({ loc, slug }: { loc: Location; slug: string }) {
  const { progress } = useEvents();
  const draw = useDrawLocationImages(slug);
  const upload = useUploadLocationImage(slug);
  const use = useUseLocationImage(slug);
  const [count, setCount] = useState(2);
  const [engine, setEngine] = useState('');
  const [preview, setPreview] = useState<number | null>(null);
  const stepDefault = useStepDefault(slug, 'location');
  const images = loc.images ?? [];
  const jobs = (loc.active_jobs ?? []).filter((j) => j.type === 'image.location');
  const failed = jobs.length === 0 ? loc.last_failure : null;
  const canDraw = hasEnglishPrompt(loc);
  const error = [draw.error, upload.error, use.error].find(Boolean);

  return (
    <div className="location-images" aria-label={`${loc.name} 的场景图`}>
      <h5>场景图</h5>
      {images.length === 0 && jobs.length === 0 && !failed ? (
        <p className="muted small">还没有场景图。出首帧时场景图排在参考图第一张，同一场景的各镜布景更一致。</p>
      ) : (
        <div className="portrait-strip" role="list" aria-label={`${loc.name} 的场景图候选`}>
          {images.map((c, i) => {
            const selected = c.id === loc.image_candidate_id;
            return (
              <div key={c.id} role="listitem" className={`pcard${selected ? ' is-selected' : ''}`}>
                <button type="button" className="pcard-open" aria-label={`场景图 #${c.id}${selected ? '（当前）' : ''}`} onClick={() => setPreview(i)}>
                  <img src={c.url} alt={`${loc.name} 场景图 ${i + 1}`} loading="lazy" />
                </button>
                <span className="pcard-tag">{c.engine === 'upload' ? '上传' : 'AI'}</span>
                {selected ? (
                  <span className="pcard-current">当前</span>
                ) : (
                  <button
                    type="button"
                    className="sm pcard-use"
                    aria-label={`把场景图 #${c.id} 设为场景图`}
                    disabled={use.isPending}
                    title="换场景图后，这个场景里的镜头首帧会过期"
                    onClick={() => use.mutate({ id: loc.id, candidateId: c.id })}
                  >
                    设为场景图
                  </button>
                )}
              </div>
            );
          })}
          {jobs.map((j) => {
            const pr = progress[j.id];
            return (
              <div key={j.id} role="listitem" className="pcard pcard-pending" data-testid={`location-drawing-${j.id}`}>
                <span className="spinner" aria-hidden="true" />
                <span>{j.status === 'running' ? '生成中' : '排队中'}</span>
                {pr && pr.max > 0 && <span className="muted">{Math.round((pr.value / pr.max) * 100)}%</span>}
              </div>
            );
          })}
          {failed && (
            <div role="listitem" className="pcard pcard-failed">
              <span className="pcard-failed-text">出图失败：{failed.error || '没有错误信息'}</span>
              <Link to={`/jobs?job=${failed.id}`}>查看任务</Link>
            </div>
          )}
        </div>
      )}
      <div className="row draw-bar">
        <label className="inline-field">
          <span>张数</span>
          <select aria-label={`${loc.name} 场景图张数`} value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <ImageEngineSelect
          value={engine}
          onChange={setEngine}
          defaultLabel={stepDefault ? `默认（${engineLabel(stepDefault)}）` : '项目图像引擎'}
          ariaLabel={`${loc.name} 场景图引擎`}
        />
        <button
          type="button"
          className="sm"
          disabled={draw.isPending || !canDraw}
          title={canDraw ? '按画面提示词出空场景（不出人物）' : '先在上面写英文画面提示词并保存'}
          onClick={() => draw.mutate({ id: loc.id, count, engine: engine || undefined })}
        >
          {jobs.length > 0 ? `出场景图（${jobs.length} 张进行中）` : '出场景图'}
        </button>
        <label className={`btn sm${upload.isPending ? ' is-disabled' : ''}`}>
          {upload.isPending ? '上传中…' : '上传场景图'}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            aria-label={`上传 ${loc.name} 场景图`}
            hidden
            disabled={upload.isPending}
            onChange={(ev) => {
              const f = ev.target.files?.[0];
              ev.target.value = '';
              if (f) upload.mutate({ id: loc.id, file: f });
            }}
          />
        </label>
      </div>
      {!canDraw && <p className="muted small">先写英文画面提示词（画面中不出人）再出场景图，也可以直接上传。</p>}
      {error && <p className="error small">{errorText(error)}</p>}
      {preview !== null && images[preview] && (
        <Lightbox
          title={`${loc.name} 场景图`}
          items={images.map((c, i) => ({
            url: c.url,
            alt: `${loc.name} 场景图 ${i + 1}`,
            current: c.id === loc.image_candidate_id,
            caption: c.engine === 'upload' ? '上传的场景图' : engineLabel(c.engine),
          }))}
          index={preview}
          onIndex={setPreview}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
