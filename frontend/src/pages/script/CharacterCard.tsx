// 角色卡：定妆照候选（上传的 + 本地 AI 抽的），单击看大图、「设为当前造型」确认后才切换；抽卡（失败时末尾有失败卡）；
// AI 推荐造型；三视图；角色资料。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useDrawPortraits,
  useEpisode,
  useJob,
  usePatchCharacter,
  useSuggestDesigns,
  useUploadPortrait,
  useUsePortrait,
} from '../../api/queries';
import type { Candidate, Character, CharacterPatch, DesignOption } from '../../api/types';
import { useConfirm } from '../../components/ConfirmDialog';
import { nextActionProps } from '../../components/EpisodeHeader';
import { EditableFields, type FieldSpec, type FieldValues, useUnsavedGuard } from '../../components/EditableFields';
import { ImageEngineSelect } from '../../components/ImageEngineSelect';
import { Lightbox } from '../../components/Lightbox';
import { VOICES } from '../../format';
import { useEvents } from '../../ws/EventsContext';
import { isDecoding } from '../../ws/events';
import { TurnaroundPanel } from './TurnaroundPanel';

const CHARACTER_FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'description', label: '人物描述', kind: 'textarea', rows: 2 },
  { name: 'visual_prompt', label: '外貌提示词（英文，会原样写进每个出场镜头）', kind: 'textarea', rows: 5 },
  { name: 'core_features', label: '核心特征', kind: 'textarea', rows: 2 },
  {
    name: 'voice',
    label: '配音音色',
    kind: 'select',
    options: [{ value: '', label: '（不配音）' }, ...VOICES.map((v) => ({ value: v.id, label: v.label }))],
    customLabel: '其他（手填）',
    placeholder: 'Edge TTS 音色，如 km-KH-PisethNeural',
  },
];

const DIRTY_HINT = '先保存或放弃修改';

/** 角色卡改动转 PATCH 体：音色留空表示不配音（null）。 */
export function toCharacterPatch(changed: FieldValues): CharacterPatch {
  const out: CharacterPatch = { ...changed };
  if (changed.voice !== undefined) out.voice = changed.voice.trim() === '' ? null : changed.voice.trim();
  return out;
}

function promptOf(c: Candidate): string {
  const vp = c.params?.visual_prompt;
  return typeof vp === 'string' ? vp : '';
}

/** 换造型 / 换提示词的确认文案：造型是全项目共用的，只数得到本集的镜头，其他集的一并提醒；本集没有出场镜头时不写过期那句。 */
function staleTail(shots: number): string {
  return shots > 0 ? `，本集出场的 ${shots} 个镜头首帧会过期，其他集出场的镜头也会过期` : '';
}

function PortraitStrip({
  c,
  onPreview,
  onUse,
  onRetry,
  switching,
  retrying,
}: {
  c: Character;
  onPreview: (index: number) => void;
  onUse: (candidate: Candidate) => void;
  onRetry: () => void;
  switching: boolean;
  retrying: boolean;
}) {
  const { progress } = useEvents();
  const portraits = c.portraits ?? [];
  const drawing = (c.active_jobs ?? []).filter((j) => j.type === 'image.portrait');
  // 正在重抽时不再显示上一次的失败
  const failed = drawing.length === 0 ? c.last_failure : null;
  const failCard = failed ? (
    <div role="listitem" className="pcard pcard-failed" data-testid="portrait-failed">
      <span className="pcard-failed-text">抽卡失败：{failed.error || '没有错误信息'}</span>
      <button type="button" className="sm" disabled={retrying || !c.visual_prompt.trim()} onClick={onRetry}>
        重试
      </button>
      <Link to={`/jobs?job=${failed.id}`}>查看任务</Link>
    </div>
  ) : null;

  if (portraits.length === 0 && drawing.length === 0) {
    // 老接口没带候选列表时，至少显示选定的那张
    return (
      <div className="portrait-strip">
        {c.portrait_url ? (
          <img className="pcard-img" src={c.portrait_url} alt={`${c.name} 定妆照`} />
        ) : (
          <div className="pcard-empty muted">还没有定妆照</div>
        )}
        {failCard && <div role="list">{failCard}</div>}
      </div>
    );
  }
  return (
    <div className="portrait-strip" role="list" aria-label={`${c.name} 的定妆照`}>
      {portraits.map((p, i) => {
        const selected = p.id === c.portrait_candidate_id;
        const vp = promptOf(p);
        return (
          <div key={p.id} role="listitem" className={`pcard${selected ? ' is-selected' : ''}`}>
            <button
              type="button"
              className="pcard-open"
              aria-label={`定妆照 #${p.id}${selected ? '（当前）' : ''}`}
              title={vp ? `看大图：${vp}` : '看大图'}
              onClick={() => onPreview(i)}
            >
              <img src={p.url} alt={selected ? `${c.name} 定妆照` : `${c.name} 定妆照候选 ${i + 1}`} loading="lazy" />
            </button>
            <span className="pcard-tag">{p.engine === 'upload' ? '上传' : 'AI'}</span>
            {selected ? (
              <span className="pcard-current">当前</span>
            ) : (
              <button
                type="button"
                className="sm pcard-use"
                aria-label={`把定妆照 #${p.id} 设为当前造型`}
                disabled={switching}
                onClick={() => onUse(p)}
              >
                设为当前造型
              </button>
            )}
          </div>
        );
      })}
      {drawing.map((j) => {
        const pr = progress[j.id];
        return (
          <div key={j.id} role="listitem" className="pcard pcard-pending" data-testid={`drawing-${j.id}`}>
            <span className="spinner" aria-hidden="true" />
            {isDecoding(pr) ? (
              <span>解码保存中…</span>
            ) : (
              <>
                <span>{j.status === 'running' ? '生成中' : '排队中'}</span>
                {pr && pr.max > 0 && <span className="muted">{Math.round((pr.value / pr.max) * 100)}%</span>}
              </>
            )}
          </div>
        );
      })}
      {failCard}
    </div>
  );
}

function OptionCard({
  opt,
  index,
  busy,
  onDraw,
  onSetPrompt,
}: {
  opt: DesignOption;
  index: number;
  busy: boolean;
  onDraw: (prompt: string) => void;
  onSetPrompt: (prompt: string) => void;
}) {
  const [text, setText] = useState(opt.visual_prompt);
  useEffect(() => setText(opt.visual_prompt), [opt.visual_prompt]);
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  return (
    <div className="option-card" data-testid={`option-${index}`}>
      <div className="option-head">
        <strong>{opt.label}</strong>
        <span className="muted">{words} 词</span>
      </div>
      {opt.core_features && <p className="option-features">{opt.core_features}</p>}
      <textarea rows={7} aria-label={`${opt.label} 提示词`} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row">
        <button type="button" className="soft" disabled={busy || !text.trim()} onClick={() => onDraw(text.trim())}>
          用这个提示词抽卡
        </button>
        <button type="button" disabled={busy || !text.trim()} onClick={() => onSetPrompt(text.trim())}>
          设为角色提示词
        </button>
      </div>
    </div>
  );
}

export function CharacterCard({
  c,
  slug,
  episodeId,
  isNext = false,
}: {
  c: Character;
  slug: string;
  episodeId: number;
  /** 页头的下一步是「抽定妆照」且轮到这个角色：抽卡按钮是本页唯一的主按钮 */
  isNext?: boolean;
}) {
  const patch = usePatchCharacter(slug);
  const upload = useUploadPortrait(slug);
  const draw = useDrawPortraits(slug);
  const use = useUsePortrait(slug);
  const suggest = useSuggestDesigns(slug);
  const confirm = useConfirm();
  const episode = useEpisode(episodeId);
  // 本集里这个角色出场的镜头数：换造型 / 换提示词后它们的首帧会过期
  const castShots = (episode.data?.shots ?? []).filter((sh) => (sh.cast ?? []).some((x) => x.character_id === c.id)).length;
  const [dirty, setDirty] = useState(false);
  useUnsavedGuard(dirty);
  const [hint, setHint] = useState<string | null>(null);
  useEffect(() => {
    if (!dirty) setHint(null);
  }, [dirty]);
  const [preview, setPreview] = useState<number | null>(null);
  const [count, setCount] = useState(2);
  const [engine, setEngine] = useState('');
  const [suggestJob, setSuggestJob] = useState<number | null>(null);
  const job = useJob(suggestJob);
  const suggesting = (c.active_jobs ?? []).some((j) => j.type === 'character.suggest');
  const drawingCount = (c.active_jobs ?? []).filter((j) => j.type === 'image.portrait').length;
  const options = c.design_options ?? [];
  const portraits = c.portraits ?? [];
  const suggestFailed = job.data && (job.data.status === 'failed' || job.data.status === 'canceled');
  const waitingInput = job.data?.status === 'waiting_input';
  const error = [patch.error, upload.error, draw.error, use.error, suggest.error].find(Boolean);
  const drawAgain = () => draw.mutate({ id: c.id, count, engine: engine || undefined });

  /** 设为当前造型：资料有没保存的修改时先提示；否则确认后才切换（带提示词的会覆盖外貌描述）。 */
  const usePortrait = async (p: Candidate) => {
    if (dirty) {
      setHint(DIRTY_HINT);
      return;
    }
    const ok = await confirm({
      title: '设为当前造型？',
      body: promptOf(p)
        ? `用这张的提示词覆盖外貌描述${staleTail(castShots)}`
        : `换成这张定妆照（外貌描述不变）${staleTail(castShots)}`,
      confirmText: '设为当前造型',
    });
    if (ok) use.mutate({ id: c.id, candidateId: p.id });
  };

  const setPrompt = async (prompt: string) => {
    if (dirty) {
      setHint(DIRTY_HINT);
      return;
    }
    const ok = await confirm({
      title: '设为角色提示词？',
      body: `用这个方案的提示词覆盖外貌描述${staleTail(castShots)}`,
      confirmText: '设为角色提示词',
    });
    if (ok) patch.mutate({ id: c.id, visual_prompt: prompt });
  };

  return (
    <article className="char-card" data-testid={`character-${c.id}`}>
      <header className="char-head">
        <h4>{c.name}</h4>
        {c.description && <p className="char-desc">{c.description}</p>}
      </header>

      <div className="char-body">
        <div className="char-looks">
          <PortraitStrip
            c={c}
            switching={use.isPending}
            retrying={draw.isPending}
            onPreview={setPreview}
            onUse={usePortrait}
            onRetry={drawAgain}
          />
          {hint && <p className="notice small">{hint}</p>}
          <p className="muted small">
            单击看大图；点「设为当前造型」才会换（提示词跟着换）。出场镜头的首帧和视频会带上选定的这张锁脸（H3 引擎）。
          </p>
          <div className="row draw-bar">
            <label className="inline-field">
              <span>张数</span>
              <select aria-label="抽卡张数" value={count} onChange={(e) => setCount(Number(e.target.value))}>
                {[1, 2, 3, 4].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <ImageEngineSelect value={engine} onChange={setEngine} defaultLabel="项目图像引擎" ariaLabel="抽卡引擎" />
            <button
              type="button"
              {...nextActionProps(isNext)}
              disabled={draw.isPending || !c.visual_prompt.trim()}
              title={c.visual_prompt.trim() ? '用角色当前的外貌提示词出正面半身定妆照（本机 ComfyUI，或选浏览器引擎走 ChatGPT / Gemini 网页）' : '先写外貌提示词或用 AI 推荐造型'}
              onClick={drawAgain}
            >
              {drawingCount > 0 ? `抽卡（${drawingCount} 张进行中）` : '抽卡'}
            </button>
            <label className={`btn${upload.isPending ? ' is-disabled' : ''}`}>
              {upload.isPending ? '上传中…' : '上传定妆照'}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label="上传定妆照"
                hidden
                disabled={upload.isPending}
                onChange={(ev) => {
                  const f = ev.target.files?.[0];
                  ev.target.value = '';
                  if (f) upload.mutate({ id: c.id, file: f });
                }}
              />
            </label>
            <button
              type="button"
              disabled={suggest.isPending || suggesting}
              onClick={() =>
                suggest.mutate({ id: c.id, count: 3, episode_id: episodeId }, { onSuccess: (r) => setSuggestJob(r.job_ids[0]) })
              }
            >
              {suggesting ? 'AI 推荐中…' : 'AI 推荐造型'}
            </button>
          </div>
          {suggestFailed && (
            <p className="error">
              推荐造型失败：{job.data?.error ?? job.data?.status} · <Link to={`/jobs?job=${suggestJob}`}>查看任务</Link>
            </p>
          )}
          {waitingInput && (
            <p className="notice">
              推荐造型在等人工输入 · <Link to={`/jobs?job=${suggestJob}`}>去填写</Link>
            </p>
          )}
        </div>

        <div className="char-fields">
          <EditableFields
            fields={CHARACTER_FIELDS}
            initial={{
              name: c.name,
              description: c.description,
              visual_prompt: c.visual_prompt,
              core_features: c.core_features,
              voice: c.voice ?? '',
            }}
            saving={patch.isPending}
            onDirtyChange={setDirty}
            onSave={(_v, ch) => patch.mutate({ id: c.id, ...toCharacterPatch(ch) })}
          />
        </div>
      </div>
      <TurnaroundPanel c={c} slug={slug} />
      {options.length > 0 && (
        <div className="options" aria-label={`${c.name} 的 AI 推荐造型`}>
          {options.map((o, i) => (
            <OptionCard
              key={`${i}-${o.label}`}
              opt={o}
              index={i}
              busy={draw.isPending || patch.isPending}
              onDraw={(prompt) => draw.mutate({ id: c.id, count, visual_prompt: prompt, engine: engine || undefined })}
              onSetPrompt={setPrompt}
            />
          ))}
        </div>
      )}
      {error && <p className="error">{errorText(error)}</p>}
      {preview !== null && portraits.length > 0 && (
        <Lightbox
          title={`${c.name} 定妆照`}
          items={portraits.map((p, i) => ({
            url: p.url,
            alt: p.id === c.portrait_candidate_id ? `${c.name} 定妆照` : `${c.name} 定妆照候选 ${i + 1}`,
            current: p.id === c.portrait_candidate_id,
            caption: promptOf(p) || (p.engine === 'upload' ? '上传的定妆照' : undefined),
          }))}
          index={preview}
          onIndex={setPreview}
          onClose={() => setPreview(null)}
          actions={(i) => {
            const p = portraits[i];
            if (!p || p.id === c.portrait_candidate_id) return null;
            return (
              <div className="row">
                <button type="button" className="primary" disabled={use.isPending} onClick={() => usePortrait(p)}>
                  设为当前造型
                </button>
                {hint && <span className="notice small">{hint}</span>}
              </div>
            );
          }}
        />
      )}
    </article>
  );
}
