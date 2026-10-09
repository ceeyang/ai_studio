// 角色卡：定妆照候选（上传的 + 本地 AI 抽的），点一张切换角色造型；抽卡；AI 推荐造型；三视图；角色资料。
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorText } from '../../api/client';
import {
  useDrawPortraits,
  useJob,
  usePatchCharacter,
  useSuggestDesigns,
  useUploadPortrait,
  useUsePortrait,
} from '../../api/queries';
import type { Candidate, Character, CharacterPatch, DesignOption } from '../../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../../components/EditableFields';
import { useEvents } from '../../ws/EventsContext';
import { isDecoding } from '../../ws/events';
import { TurnaroundPanel } from './TurnaroundPanel';

const CHARACTER_FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'description', label: '人物描述', kind: 'textarea', rows: 2 },
  { name: 'visual_prompt', label: '外貌提示词（英文，会原样写进每个出场镜头）', kind: 'textarea', rows: 5 },
  { name: 'core_features', label: '核心特征', kind: 'textarea', rows: 2 },
  { name: 'voice', label: '配音音色（Edge TTS，如 km-KH-PisethNeural 男 / km-KH-SreymomNeural 女）' },
];

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

function PortraitStrip({
  c,
  onUse,
  switching,
}: {
  c: Character;
  onUse: (candidateId: number) => void;
  switching: boolean;
}) {
  const { progress } = useEvents();
  const portraits = c.portraits ?? [];
  const drawing = (c.active_jobs ?? []).filter((j) => j.type === 'image.portrait');
  if (portraits.length === 0 && drawing.length === 0) {
    // 老接口没带候选列表时，至少显示选定的那张
    return c.portrait_url ? (
      <div className="portrait-strip">
        <img className="pcard-img" src={c.portrait_url} alt={`${c.name} 定妆照`} />
      </div>
    ) : (
      <div className="portrait-strip">
        <div className="pcard-empty muted">还没有定妆照</div>
      </div>
    );
  }
  return (
    <div className="portrait-strip" role="list" aria-label={`${c.name} 的定妆照`}>
      {portraits.map((p, i) => {
        const selected = p.id === c.portrait_candidate_id;
        const vp = promptOf(p);
        return (
          <button
            key={p.id}
            type="button"
            role="listitem"
            className={`pcard${selected ? ' is-selected' : ''}`}
            aria-pressed={selected}
            aria-label={`定妆照 #${p.id}${selected ? '（当前）' : ''}`}
            title={selected ? '当前造型' : vp ? `切换到这个造型：${vp}` : '切换到这张定妆照'}
            disabled={switching}
            onClick={() => {
              if (!selected) onUse(p.id);
            }}
          >
            <img src={p.url} alt={selected ? `${c.name} 定妆照` : `${c.name} 定妆照候选 ${i + 1}`} loading="lazy" />
            <span className="pcard-tag">{p.engine === 'upload' ? '上传' : 'AI'}</span>
            {selected && <span className="pcard-current">当前</span>}
          </button>
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
        <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => onDraw(text.trim())}>
          用这个提示词抽卡
        </button>
        <button type="button" disabled={busy || !text.trim()} onClick={() => onSetPrompt(text.trim())}>
          设为角色提示词
        </button>
      </div>
    </div>
  );
}

export function CharacterCard({ c, slug, episodeId }: { c: Character; slug: string; episodeId: number }) {
  const patch = usePatchCharacter(slug);
  const upload = useUploadPortrait(slug);
  const draw = useDrawPortraits(slug);
  const use = useUsePortrait(slug);
  const suggest = useSuggestDesigns(slug);
  const [count, setCount] = useState(2);
  const [suggestJob, setSuggestJob] = useState<number | null>(null);
  const job = useJob(suggestJob);
  const suggesting = (c.active_jobs ?? []).some((j) => j.type === 'character.suggest');
  const drawingCount = (c.active_jobs ?? []).filter((j) => j.type === 'image.portrait').length;
  const options = c.design_options ?? [];
  const suggestFailed = job.data && (job.data.status === 'failed' || job.data.status === 'canceled');
  const waitingInput = job.data?.status === 'waiting_input';
  const error = [patch.error, upload.error, draw.error, use.error, suggest.error].find(Boolean);

  return (
    <article className="char-card" data-testid={`character-${c.id}`}>
      <header className="char-head">
        <h4>{c.name}</h4>
        {c.description && <p className="char-desc">{c.description}</p>}
      </header>

      <div className="char-body">
        <div className="char-looks">
          <PortraitStrip c={c} switching={use.isPending} onUse={(candidateId) => use.mutate({ id: c.id, candidateId })} />
          <p className="muted small">点一张卡切换角色造型（提示词跟着换）；出场镜头的首帧和视频会带上选定的这张锁脸（H3 引擎）。</p>
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
            <button
              type="button"
              className="primary"
              disabled={draw.isPending || !c.visual_prompt.trim()}
              title={c.visual_prompt.trim() ? '用角色当前的外貌提示词，在本机 ComfyUI 出正面半身定妆照' : '先写外貌提示词或用 AI 推荐造型'}
              onClick={() => draw.mutate({ id: c.id, count })}
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
              onDraw={(prompt) => draw.mutate({ id: c.id, count, visual_prompt: prompt })}
              onSetPrompt={(prompt) => patch.mutate({ id: c.id, visual_prompt: prompt })}
            />
          ))}
        </div>
      )}
      {error && <p className="error">{errorText(error)}</p>}
    </article>
  );
}
