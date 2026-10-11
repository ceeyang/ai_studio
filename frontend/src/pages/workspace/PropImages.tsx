// 设定里的「道具」：分镜里出现过的道具（镜头 props 的英文说法），每个道具可以上传或用图像引擎出一张
// 「只拍道具、白底」的道具图。镜头里带了这个道具时，出首帧把道具图排在出场角色后面一起给引擎。
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import { useDrawPropImages, usePatchProp, useStepDefault, useUploadPropImage, useUsePropImage } from '../../api/queries';
import type { Prop, PropPatch } from '../../api/types';
import { EditableFields, type FieldSpec } from '../../components/EditableFields';
import { ImageEngineSelect } from '../../components/ImageEngineSelect';
import { Lightbox } from '../../components/Lightbox';
import { WebConversation } from '../../components/WebConversation';
import { engineLabel } from '../../format';
import { useEvents } from '../../ws/EventsContext';

const PROP_FIELDS: FieldSpec[] = [
  { name: 'visual_prompt', label: '补充描述（英文，可不填：材质、颜色、磨损……）', kind: 'textarea', rows: 2 },
];

const CJK = /[⺀-鿿가-힯豈-﫿＀-￯]/;

/** 道具有没有英文说法（名字或补充描述）：没有就出不了图 */
export function propDrawable(p: Prop): boolean {
  const en = (s: string) => !!s.trim() && !CJK.test(s);
  return en(p.name) || en(p.visual_prompt);
}

/** 用到的镜头：「第1集 S01、S03」 */
export function propUses(p: Prop): string {
  const byEp = new Map<number, string[]>();
  for (const u of p.uses ?? []) byEp.set(u.episode_number, [...(byEp.get(u.episode_number) ?? []), u.shot_code]);
  return [...byEp.entries()].map(([n, codes]) => `第${n}集 ${codes.join('、')}`).join('；');
}

export function PropCard({ prop, slug, onDirtyChange }: { prop: Prop; slug: string; onDirtyChange?(dirty: boolean): void }) {
  const { progress } = useEvents();
  const patch = usePatchProp(slug);
  const draw = useDrawPropImages(slug);
  const upload = useUploadPropImage(slug);
  const use = useUsePropImage(slug);
  const [count, setCount] = useState(1);
  const [engine, setEngine] = useState('');
  // 网页出图：这次不续接之前的网页对话（提交后复位）
  const [newConv, setNewConv] = useState(false);
  const [preview, setPreview] = useState<number | null>(null);
  const stepDefault = useStepDefault(slug, 'prop');
  const images = prop.images ?? [];
  const jobs = (prop.active_jobs ?? []).filter((j) => j.type === 'image.prop');
  const failed = jobs.length === 0 ? prop.last_failure : null;
  const canDraw = propDrawable(prop);
  const error = [draw.error, upload.error, use.error].find(Boolean);
  const uses = propUses(prop);

  return (
    <div className="card prop-card" data-testid={`prop-${prop.id}`} aria-label={`道具 ${prop.name}`}>
      <p className="prop-name">
        <b>{prop.name}</b>
        {prop.image_stale && <span className="badge stale">已过期</span>}
      </p>
      {uses && <p className="muted small">用在：{uses}</p>}
      <EditableFields
        fields={PROP_FIELDS}
        initial={{ visual_prompt: prop.visual_prompt }}
        saving={patch.isPending}
        error={patch.error ? errorText(patch.error) : null}
        onSave={(_v, ch) => patch.mutate({ id: prop.id, ...(ch as PropPatch) })}
        onDirtyChange={onDirtyChange}
      />
      <div className="location-images" aria-label={`${prop.name} 的道具图`}>
        <h5>道具图</h5>
        {prop.image_stale && (
          <p className="warn small">道具图已过期：选定的这张是按改之前的描述出的，重出一张再设为道具图（换了以后带这个道具的镜头首帧会过期）。</p>
        )}
        {images.length === 0 && jobs.length === 0 && !failed ? (
          <p className="muted small">还没有道具图。镜头里带了这个道具时，道具图排在角色后面给引擎，各镜里的道具长得一样。</p>
        ) : (
          <div className="portrait-strip" role="list" aria-label={`${prop.name} 的道具图候选`}>
            {images.map((c, i) => {
              const selected = c.id === prop.image_candidate_id;
              return (
                <div key={c.id} role="listitem" className={`pcard${selected ? ' is-selected' : ''}`}>
                  <button type="button" className="pcard-open" aria-label={`道具图 #${c.id}${selected ? '（当前）' : ''}`} onClick={() => setPreview(i)}>
                    <img src={c.url} alt={`${prop.name} 道具图 ${i + 1}`} loading="lazy" />
                  </button>
                  <span className="pcard-tag">{c.engine === 'upload' ? '上传' : 'AI'}</span>
                  {selected ? (
                    <span className="pcard-current">当前</span>
                  ) : (
                    <button
                      type="button"
                      className="sm pcard-use"
                      aria-label={`把道具图 #${c.id} 设为道具图`}
                      disabled={use.isPending}
                      title="换道具图后，带这个道具的镜头首帧会过期"
                      onClick={() => use.mutate({ id: prop.id, candidateId: c.id })}
                    >
                      设为道具图
                    </button>
                  )}
                </div>
              );
            })}
            {jobs.map((j) => {
              const pr = progress[j.id];
              return (
                <div key={j.id} role="listitem" className="pcard pcard-pending" data-testid={`prop-drawing-${j.id}`}>
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
            <select aria-label={`${prop.name} 道具图张数`} value={count} onChange={(e) => setCount(Number(e.target.value))}>
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
            ariaLabel={`${prop.name} 道具图引擎`}
          />
          <button
            type="button"
            className="sm"
            disabled={draw.isPending || !canDraw}
            title={canDraw ? '只拍这个道具、白底' : '道具还没有英文说法，先写英文补充描述并保存'}
            onClick={() =>
              draw.mutate(
                { id: prop.id, count, engine: engine || undefined, new_conversation: newConv || undefined },
                { onSuccess: () => setNewConv(false) },
              )
            }
          >
            {jobs.length > 0 ? `出道具图（${jobs.length} 张进行中）` : '出道具图'}
          </button>
          <label className={`btn sm${upload.isPending ? ' is-disabled' : ''}`}>
            {upload.isPending ? '上传中…' : '上传道具图'}
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              aria-label={`上传 ${prop.name} 道具图`}
              hidden
              disabled={upload.isPending}
              onChange={(ev) => {
                const f = ev.target.files?.[0];
                ev.target.value = '';
                if (f) upload.mutate({ id: prop.id, file: f });
              }}
            />
          </label>
        </div>
        <WebConversation
          scopeKind="prop"
          scopeId={prop.id}
          engine={engine || stepDefault}
          newConversation={newConv}
          onNewConversationChange={setNewConv}
          label={`${prop.name} 道具图`}
        />
        {error && <p className="error small">{errorText(error)}</p>}
      </div>
      {preview !== null && images[preview] && (
        <Lightbox
          title={`${prop.name} 道具图`}
          items={images.map((c, i) => ({
            url: c.url,
            alt: `${prop.name} 道具图 ${i + 1}`,
            current: c.id === prop.image_candidate_id,
            caption: c.engine === 'upload' ? '上传的道具图' : engineLabel(c.engine),
          }))}
          index={preview}
          onIndex={setPreview}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}
