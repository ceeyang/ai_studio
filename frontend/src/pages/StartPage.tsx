// 从一个点子开始（/start）：一句话点子 → AI 出几个方向 → 扩写成梗概 → 确认后建项目、排剧本和分镜，
// 跳到工作台（?autopilot=1，分镜做完在那里点「继续出片」）。
// 方向、梗概只在页面里（「开始制作」前不写库）；草稿记在本机（localStorage），刷新能接着来。
import { type FormEvent, useEffect, useId, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { errorText } from '../api/client';
import { useIdeaExpand, useIdeaOptions, useIdeaStart } from '../api/queries';
import type { Aspect, IdeaOption, StylePresetKey } from '../api/types';
import { EngineNotes, EngineSelects, useEngineChoice } from '../components/EngineChoice';
import { DEFAULT_STYLE_PRESET, STYLE_PRESETS, StylePresetPicker, stylePresetLabel } from '../components/StylePresetPicker';
import { workspacePath } from './workspace/urls';

type Step = 1 | 2 | 3 | 4;

const SECONDS = [10, 15, 30, 60] as const;
const ASPECTS: { value: Aspect; label: string }[] = [
  { value: '9:16', label: '9:16 竖屏' },
  { value: '16:9', label: '16:9 横屏' },
];
const STEPS: { step: Step; label: string }[] = [
  { step: 1, label: '点子' },
  { step: 2, label: '方向' },
  { step: 3, label: '梗概' },
  { step: 4, label: '确认' },
];
/** 一次出几个方向 */
const BATCH = 4;

export interface IdeaDraft {
  step: Step;
  idea: string;
  seconds: number;
  aspect: Aspect;
  preset: StylePresetKey;
  /** 出过的方向卡片（「再来 4 个」接在后面） */
  options: IdeaOption[];
  /** 选中的卡片下标（最多两个，按点选顺序） */
  picked: number[];
  title: string;
  synopsis: string;
}

export const DRAFT_KEY = 'studio.ideaDraft';

const EMPTY: IdeaDraft = {
  step: 1,
  idea: '',
  seconds: 15,
  aspect: '9:16',
  preset: DEFAULT_STYLE_PRESET,
  options: [],
  picked: [],
  title: '',
  synopsis: '',
};

/** 读本机草稿；读不到、格式不对就从头来 */
function readDraft(): IdeaDraft {
  try {
    const v = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? 'null') as Partial<IdeaDraft> | null;
    if (!v || typeof v !== 'object') return EMPTY;
    const options = Array.isArray(v.options) ? v.options.filter((o) => o && typeof o.title === 'string') : [];
    const picked = Array.isArray(v.picked) ? v.picked.filter((i) => typeof i === 'number' && i >= 0 && i < options.length).slice(0, 2) : [];
    return {
      step: v.step === 2 || v.step === 3 || v.step === 4 ? v.step : 1,
      idea: typeof v.idea === 'string' ? v.idea : '',
      seconds: SECONDS.includes(v.seconds as (typeof SECONDS)[number]) ? (v.seconds as number) : EMPTY.seconds,
      aspect: v.aspect === '16:9' ? '16:9' : '9:16',
      preset: STYLE_PRESETS.some((p) => p.key === v.preset) ? (v.preset as StylePresetKey) : EMPTY.preset,
      options,
      picked,
      title: typeof v.title === 'string' ? v.title : '',
      synopsis: typeof v.synopsis === 'string' ? v.synopsis : '',
    };
  } catch {
    return EMPTY;
  }
}

function writeDraft(d: IdeaDraft): void {
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  } catch {
    // 隐私模式或存储被禁用：不记也不影响这次用
  }
}

function clearDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {
    // 同上
  }
}

/** 几个一样样式的单选（时长、画幅）：用风格预设的小卡片样式 */
function ChoiceGroup<T extends string | number>({
  legend,
  name,
  value,
  choices,
  onChange,
}: {
  legend: string;
  name: string;
  value: T;
  choices: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <fieldset className="preset-picker">
      <legend>{legend}</legend>
      <div className="preset-grid idea-choice-grid" style={{ gridTemplateColumns: `repeat(${choices.length}, minmax(0, 1fr))` }}>
        {choices.map((c) => (
          <label key={String(c.value)} className={c.value === value ? 'preset-option is-selected' : 'preset-option'}>
            <input type="radio" name={name} checked={c.value === value} onChange={() => onChange(c.value)} />
            <span className="preset-name">{c.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function OptionCard({ option, order, onToggle }: { option: IdeaOption; order: number; onToggle: () => void }) {
  const checked = order > 0;
  return (
    <label className={checked ? 'idea-card is-selected' : 'idea-card'}>
      <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`选「${option.title}」`} />
      <span className="idea-card-head">
        <span className="idea-card-title">{option.title}</span>
        {option.genre && <span className="chip">{option.genre}</span>}
        {checked && <span className="idea-card-order">{order}</span>}
      </span>
      <span className="idea-card-logline">{option.logline}</span>
      <dl className="idea-card-beats">
        {option.hook && (
          <>
            <dt>钩子</dt>
            <dd>{option.hook}</dd>
          </>
        )}
        {option.twist && (
          <>
            <dt>反转</dt>
            <dd>{option.twist}</dd>
          </>
        )}
        {option.ending && (
          <>
            <dt>结尾</dt>
            <dd>{option.ending}</dd>
          </>
        )}
      </dl>
    </label>
  );
}

export function StartPage() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState<IdeaDraft>(readDraft);
  const { step, idea, seconds, aspect, preset, options, picked, title, synopsis } = draft;
  const set = (patch: Partial<IdeaDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const [feedback, setFeedback] = useState('');
  const [instruction, setInstruction] = useState('');
  // 出方向的按钮有三个（出几个方向 / 再来 4 个 / 按要求重出）：记住是哪个在等，只让它显示「正在想…」
  const [asking, setAsking] = useState<'first' | 'more' | 'feedback' | null>(null);
  const [writing, setWriting] = useState<'expand' | 'rewrite' | null>(null);
  const ask = useIdeaOptions();
  const expand = useIdeaExpand();
  const start = useIdeaStart();
  const engine = useEngineChoice(preset);
  const ideaId = useId();
  const synopsisId = useId();

  useEffect(() => writeDraft(draft), [draft]);

  const base = { idea: idea.trim(), target_seconds: seconds, style_preset: preset };
  const pickedOptions = picked.map((i) => options[i]).filter(Boolean);
  const busy = ask.isPending || expand.isPending || start.isPending;

  // 第一次出方向：换了点子就从头来，卡片和选择都清掉
  const askFirst = (e: FormEvent) => {
    e.preventDefault();
    if (!idea.trim() || busy) return;
    setAsking('first');
    ask.mutate(
      { ...base, count: BATCH },
      {
        onSuccess: (r) => set({ options: r.options, picked: [], step: 2 }),
        onSettled: () => setAsking(null),
      },
    );
  };

  // 再来几个 / 按要求重出：带上看过的标题让模型避开，新卡片接在后面，之前的还在
  const askMore = (kind: 'more' | 'feedback') => {
    setAsking(kind);
    const titles = options.map((o) => o.title);
    ask.mutate(
      { ...base, count: BATCH, exclude: titles, ...(kind === 'feedback' ? { feedback: feedback.trim() } : {}) },
      {
        onSuccess: (r) => {
          setDraft((d) => ({ ...d, options: [...d.options, ...r.options] }));
          if (kind === 'feedback') setFeedback('');
        },
        onSettled: () => setAsking(null),
      },
    );
  };

  // 单选一张，或勾两张合并；已经两张时再点第三张，换掉先点的那张
  const toggle = (i: number) =>
    setDraft((d) => {
      if (d.picked.includes(i)) return { ...d, picked: d.picked.filter((x) => x !== i) };
      return { ...d, picked: [...d.picked, i].slice(-2) };
    });

  const writeSynopsis = () => {
    if (pickedOptions.length === 0 || busy) return;
    setWriting('expand');
    expand.mutate(
      { ...base, options: pickedOptions },
      {
        onSuccess: (r) => set({ title: r.title, synopsis: r.synopsis, step: 3 }),
        onSettled: () => setWriting(null),
      },
    );
  };

  // 按修改要求改写：片名用户可能改过，只换梗概
  const rewrite = (e: FormEvent) => {
    e.preventDefault();
    if (!instruction.trim() || !synopsis.trim() || pickedOptions.length === 0 || busy) return;
    setWriting('rewrite');
    expand.mutate(
      { ...base, options: pickedOptions, current: synopsis, instruction: instruction.trim() },
      {
        onSuccess: (r) => {
          set({ synopsis: r.synopsis });
          setInstruction('');
        },
        onSettled: () => setWriting(null),
      },
    );
  };

  const begin = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !synopsis.trim() || busy) return;
    start.mutate(
      {
        title: title.trim(),
        synopsis: synopsis.trim(),
        target_seconds: seconds,
        aspect,
        style_preset: preset,
        image_engine: engine.image || null,
        video_engine: engine.video || null,
      },
      {
        onSuccess: (r) => {
          clearDraft();
          navigate(`${workspacePath(r.slug, r.episode_id)}?autopilot=1`);
        },
      },
    );
  };

  const restart = () => {
    clearDraft();
    setDraft(EMPTY);
    setFeedback('');
    setInstruction('');
    ask.reset();
    expand.reset();
    start.reset();
  };

  // 能去哪一步：有卡片才能看方向，有梗概才能看梗概和确认
  const reachable = (s: Step) => s === 1 || (s === 2 && options.length > 0) || (s >= 3 && synopsis.trim() !== '');
  const goto = (s: Step) => {
    if (reachable(s) && !busy) set({ step: s });
  };
  const doneStep = (s: Step) => s < step && reachable(s);

  return (
    <div className="page idea-wizard">
      <header className="page-head hero-head">
        <span className="eyebrow">
          <Link to="/">项目中心</Link> · 从一个点子开始
        </span>
        <h2>从一个点子开始</h2>
        <button type="button" className="ghost sm head-action" onClick={restart} disabled={busy}>
          重新开始
        </button>
      </header>
      <ol className="stepper idea-steps" aria-label="向导步骤">
        {STEPS.map(({ step: s, label }) => (
          <li key={s} className={`step ${doneStep(s) ? 'is-done' : s === step ? 'is-active' : 'is-todo'}${s === step ? ' is-here' : ''}`}>
            <button type="button" aria-current={s === step ? 'step' : undefined} disabled={!reachable(s) || busy} onClick={() => goto(s)}>
              <span className="step-dot" aria-hidden="true">
                {doneStep(s) ? '✓' : s}
              </span>
              <span className="step-text">
                <span className="step-label">{label}</span>
              </span>
            </button>
          </li>
        ))}
      </ol>

      {step === 1 && (
        <section className="panel">
          <form className="fields" onSubmit={askFirst}>
            <div className="field">
              <label htmlFor={ideaId}>一句话点子</label>
              <textarea
                id={ideaId}
                rows={3}
                value={idea}
                onChange={(e) => set({ idea: e.target.value })}
                placeholder="如：外卖员送错了一单，收件人竟是十年后的自己"
              />
            </div>
            <ChoiceGroup
              legend="时长"
              name="idea-seconds"
              value={seconds}
              choices={SECONDS.map((s) => ({ value: s, label: `${s} 秒` }))}
              onChange={(v) => set({ seconds: v })}
            />
            <ChoiceGroup legend="画幅" name="idea-aspect" value={aspect} choices={ASPECTS} onChange={(v) => set({ aspect: v })} />
            <StylePresetPicker value={preset} onChange={(v) => set({ preset: v })} name="idea-preset" />
            {ask.error && <p className="error">{errorText(ask.error)}</p>}
            <div className="modal-foot">
              <span className="muted">AI 按点子、时长和风格出 {BATCH} 个差异明显的方向，要十几秒到一分钟。</span>
              <button type="submit" className="primary lg" disabled={!idea.trim() || busy}>
                {asking === 'first' ? '正在想…' : '出几个方向'}
              </button>
            </div>
          </form>
        </section>
      )}

      {step === 2 && (
        <section className="panel">
          <p className="muted small idea-recap">
            点子：{idea} · {seconds} 秒 · {aspect} · {stylePresetLabel(preset)}
          </p>
          <p className="muted small">点一张卡片用这个方向；勾两张会把它们合并成一个故事。</p>
          <div className="idea-cards" role="group" aria-label="方向">
            {options.map((o, i) => (
              <OptionCard key={`${i}-${o.title}`} option={o} order={picked.indexOf(i) + 1} onToggle={() => toggle(i)} />
            ))}
          </div>
          <div className="idea-more">
            <button type="button" disabled={busy} onClick={() => askMore('more')}>
              {asking === 'more' ? '正在想…' : `再来 ${BATCH} 个`}
            </button>
            <form
              className="idea-inline"
              onSubmit={(e) => {
                e.preventDefault();
                if (feedback.trim() && !busy) askMore('feedback');
              }}
            >
              <input
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="补一句要求重出，如：要更搞笑、结尾要反转"
                aria-label="补一句要求重出"
              />
              <button type="submit" disabled={!feedback.trim() || busy}>
                {asking === 'feedback' ? '正在想…' : '按要求重出'}
              </button>
            </form>
          </div>
          {ask.error && <p className="error">{errorText(ask.error)}</p>}
          {expand.error && <p className="error">{errorText(expand.error)}</p>}
          <div className="modal-foot">
            <button type="button" className="ghost" disabled={busy} onClick={() => set({ step: 1 })}>
              改点子
            </button>
            <button type="button" className="primary lg" disabled={pickedOptions.length === 0 || busy} onClick={writeSynopsis}>
              {writing === 'expand' ? '正在写…' : pickedOptions.length === 2 ? '合并这两个写梗概' : '用这个方向写梗概'}
            </button>
          </div>
        </section>
      )}

      {step === 3 && (
        <section className="panel">
          <div className="fields">
            <label className="field">
              <span>片名</span>
              <input value={title} onChange={(e) => set({ title: e.target.value })} />
            </label>
            <div className="field">
              <label htmlFor={synopsisId}>梗概</label>
              <textarea id={synopsisId} rows={12} value={synopsis} onChange={(e) => set({ synopsis: e.target.value })} />
              <p className="muted small">
                {synopsis.trim() ? `${Array.from(synopsis.trim()).length} 字 · ` : ''}可以直接改文字，也可以写一句修改要求让 AI 重写。
              </p>
            </div>
            <form className="idea-inline" onSubmit={rewrite}>
              <input
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="修改要求，如：主角换成女生、节奏再快一点"
                aria-label="修改要求"
              />
              <button type="submit" disabled={!instruction.trim() || !synopsis.trim() || busy}>
                {writing === 'rewrite' ? '正在写…' : '按要求改写'}
              </button>
            </form>
            {expand.error && <p className="error">{errorText(expand.error)}</p>}
            <div className="modal-foot">
              <button type="button" className="ghost" disabled={busy} onClick={() => set({ step: 2 })}>
                换个方向
              </button>
              <button type="button" className="primary lg" disabled={!title.trim() || !synopsis.trim() || busy} onClick={() => set({ step: 4 })}>
                下一步：确认
              </button>
            </div>
          </div>
        </section>
      )}

      {step === 4 && (
        <section className="panel">
          <form className="fields" onSubmit={begin}>
            <label className="field">
              <span>项目名</span>
              <input value={title} onChange={(e) => set({ title: e.target.value })} />
            </label>
            <p className="muted small">
              {seconds} 秒 · {aspect === '9:16' ? '9:16 竖屏' : '16:9 横屏'} · {stylePresetLabel(preset)}；梗概当第 1 集的原文。
            </p>
            <div className="field-grid two">
              <EngineSelects choice={engine} />
            </div>
            <EngineNotes choice={engine} />
            {start.error && <p className="error">{errorText(start.error)}</p>}
            <div className="modal-foot">
              <span className="muted">建好项目先写剧本和分镜，做完停下来给你看，没问题再继续出片。</span>
              <button type="button" className="ghost" disabled={busy} onClick={() => set({ step: 3 })}>
                改梗概
              </button>
              <button
                type="submit"
                className="primary lg"
                disabled={!title.trim() || !synopsis.trim() || engine.settingsLoading || busy}
              >
                {start.isPending ? '正在建项目…' : '开始制作'}
              </button>
            </div>
          </form>
        </section>
      )}
    </div>
  );
}
