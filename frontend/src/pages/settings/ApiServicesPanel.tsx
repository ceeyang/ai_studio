import { useEffect, useState } from 'react';
import { FoldPanel } from './FoldPanel';
import { errorText } from '../../api/client';
import { useApiServices, useSaveApiService, useTestApiService } from '../../api/queries';
import type { ApiModelKind, ApiService, ApiServiceIn } from '../../api/types';

/** 设置页「API 服务」：OpenAI（GPT）、Anthropic（Claude）、Google（Gemini）、火山方舟（Seedance）、可灵的
 *  Key、Base URL、区域（国内 / 海外）、模型名。可灵还能填 AccessKey + SecretKey（没填 API Key 时用它们签名）。
 *  后端只回掩码（sk-…abcd），这里也从不显示明文；Key 输入框留空 = 不改。 */

const KINDS: ApiModelKind[] = ['text', 'image', 'video'];

const USES: Record<string, string> = {
  openai: '文本模型（GPT，能看图）· 出图（gpt-image-1，文生图 / 参考图）· 视频（Sora，首帧图生视频）',
  anthropic: '文本模型（Claude，能看图）。Claude 没有出图、视频',
  gemini: '文本模型（Gemini，能看图）· 出图（Gemini 图像，文生图 / 参考图）· 视频（Veo，首帧图生视频）',
  ark: '视频（Seedance，首帧 / 首尾帧图生视频，有声）。国内是火山方舟，海外是 BytePlus ModelArk，Key 两边不通用',
  kling: '视频（可灵，首帧 / 首尾帧图生视频，有声）。API Key 和 AccessKey + SecretKey 填一种就行，两种都填时用 API Key',
};

interface Draft {
  key: string;
  base: string;
  models: Partial<Record<ApiModelKind, string>>;
  region?: string;
  ak?: string;
  sk?: string;
}

function draftOf(s: ApiService): Draft {
  return {
    key: '',
    base: s.base_url ?? '',
    models: Object.fromEntries(KINDS.filter((k) => s.models[k]).map((k) => [k, s.models[k]?.value ?? ''])),
    region: s.region ?? '',
    ak: '',
    sk: '',
  };
}

/** 只把改过的字段变成 PUT 体：Key 空 = 不改；Base URL / 模型名清空 = 恢复默认（发空串）；
 *  区域只在有区域选项、选的和现在的不一样时发；AccessKey / SecretKey 填了才发 */
export function servicePatch(s: ApiService, d: Draft): ApiServiceIn {
  const out: ApiServiceIn = {};
  if (d.key.trim()) out.api_key = d.key.trim();
  if (s.regions?.length && d.region && d.region !== (s.region ?? '')) out.region = d.region;
  if (s.aksk) {
    if (d.ak?.trim()) out.access_key = d.ak.trim();
    if (d.sk?.trim()) out.secret_key = d.sk.trim();
  }
  if (d.base.trim() !== (s.base_url ?? '')) out.base_url = d.base.trim();
  const models: Partial<Record<ApiModelKind, string>> = {};
  for (const k of KINDS) {
    const m = s.models[k];
    if (m && (d.models[k] ?? '').trim() !== (m.value ?? '')) models[k] = (d.models[k] ?? '').trim();
  }
  if (Object.keys(models).length) out.models = models;
  return out;
}

function sourceText(s: ApiService): string {
  if (!s.configured) return '未配置';
  const where = s.source === 'settings' ? '设置页填的' : `环境变量 ${s.env_name ?? ''}`;
  const how = s.auth === 'aksk' ? ' · AccessKey + SecretKey 签名' : '';
  return `已配置 · ${where}${how} · ${s.masked ?? ''}`;
}

/** AccessKey / SecretKey 只填了一个、另一个也没存过：保存不了 */
export function akskIncomplete(s: ApiService, d: Draft): boolean {
  if (!s.aksk) return false;
  const ak = (d.ak ?? '').trim() !== '';
  const sk = (d.sk ?? '').trim() !== '';
  return ak !== sk && !s.has_saved_aksk;
}

function ServiceCard({ s }: { s: ApiService }) {
  const save = useSaveApiService();
  const test = useTestApiService();
  const [draft, setDraft] = useState<Draft>(() => draftOf(s));
  const [show, setShow] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  // 后端数据真的变了（保存、清除、别处改了）才重置草稿；同样内容的重新拉取不打断正在输入的
  const sig = JSON.stringify({ ...s, last_test: null });
  useEffect(() => {
    setDraft(draftOf(s));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  const patch = servicePatch(s, draft);
  const dirty = Object.keys(patch).length > 0;
  const halfPair = akskIncomplete(s, draft);
  const regions = s.regions ?? [];
  const defaultBase = regions.find((r) => r.id === (draft.region || s.region))?.base_url ?? s.default_base_url;
  const edit = (d: Partial<Draft>) => {
    setJustSaved(false);
    setDraft({ ...draft, ...d });
  };
  const onSave = () => save.mutate({ id: s.id, body: patch }, { onSuccess: () => setJustSaved(true) });
  const onClear = () => save.mutate({ id: s.id, body: { clear_key: true } }, { onSuccess: () => setJustSaved(true) });
  const result = test.data ?? (s.last_test ? { ...s.last_test } : null);
  const busy = save.isPending || test.isPending;
  const id = `api-${s.id}`;

  return (
    <div className="api-service" data-testid={id}>
      <div className="api-service-head">
        <h3>{s.label}</h3>
        <span className={`pill ${s.configured ? 'pill-ok' : ''}`} data-testid={`${id}-status`}>
          {sourceText(s)}
        </span>
      </div>
      <p className="muted small">{USES[s.id] ?? ''}</p>
      <div className="api-fields">
        {regions.length > 0 && (
          <label className="field">
            <span>区域</span>
            <select aria-label={`${s.label} 区域`} value={draft.region || s.region || regions[0].id} onChange={(e) => edit({ region: e.target.value })}>
              {regions.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
            <small className="muted">决定默认的 Base URL；下面自己填了 Base URL 时以填的为准</small>
          </label>
        )}
        <label className="field">
          <span>API Key</span>
          <span className="api-key-row">
            <input
              type={show ? 'text' : 'password'}
              aria-label={`${s.label} API Key`}
              autoComplete="off"
              spellCheck={false}
              placeholder={s.configured ? `已配置（${s.masked ?? ''}），留空不改` : `粘贴 API Key（或设环境变量 ${s.env_keys.join(' / ')}）`}
              value={draft.key}
              onChange={(e) => edit({ key: e.target.value })}
            />
            <button type="button" onClick={() => setShow(!show)} aria-label={show ? '隐藏 Key' : '显示 Key'}>
              {show ? '隐藏' : '显示'}
            </button>
            {s.has_saved_key && (
              <button
                type="button"
                onClick={onClear}
                disabled={busy}
                title={s.aksk ? '删掉设置页存的 API Key 和 AccessKey / SecretKey（环境变量里的照样能用）' : '删掉设置页存的 Key（环境变量里的照样能用）'}
              >
                清除
              </button>
            )}
          </span>
        </label>
        {s.aksk && (
          <>
            <label className="field">
              <span>AccessKey（不填 API Key 时用）</span>
              <input
                type={show ? 'text' : 'password'}
                aria-label={`${s.label} AccessKey`}
                autoComplete="off"
                spellCheck={false}
                placeholder={s.has_saved_aksk ? '已保存，留空不改' : `粘贴 AccessKey（或设环境变量 ${s.env_aksk?.[0] ?? ''}）`}
                value={draft.ak ?? ''}
                onChange={(e) => edit({ ak: e.target.value })}
              />
            </label>
            <label className="field">
              <span>SecretKey</span>
              <input
                type={show ? 'text' : 'password'}
                aria-label={`${s.label} SecretKey`}
                autoComplete="off"
                spellCheck={false}
                placeholder={s.has_saved_aksk ? '已保存，留空不改' : `粘贴 SecretKey（或设环境变量 ${s.env_aksk?.[1] ?? ''}）`}
                value={draft.sk ?? ''}
                onChange={(e) => edit({ sk: e.target.value })}
              />
              <small className="muted">用 AccessKey + SecretKey 时，每次请求现签一个 30 分钟有效的令牌，不存盘</small>
            </label>
          </>
        )}
        <label className="field">
          <span>Base URL（可选）</span>
          <input
            type="text"
            aria-label={`${s.label} Base URL`}
            placeholder={defaultBase}
            value={draft.base}
            onChange={(e) => edit({ base: e.target.value })}
          />
          <small className="muted">{s.base_hint}。留空用官方地址</small>
        </label>
        {KINDS.map((k) => {
          const m = s.models[k];
          if (!m) return null;
          const listId = `${id}-${k}-models`;
          return (
            <label className="field" key={k}>
              <span>{`${m.label}名`}</span>
              <input
                type="text"
                aria-label={`${s.label} ${m.label}`}
                list={listId}
                placeholder={`默认 ${m.default}`}
                value={draft.models[k] ?? ''}
                onChange={(e) => edit({ models: { ...draft.models, [k]: e.target.value } })}
              />
              <datalist id={listId}>
                {m.suggestions.map((x) => (
                  <option key={x} value={x} />
                ))}
              </datalist>
            </label>
          );
        })}
      </div>
      <div className="row api-actions">
        <button type="button" disabled={!dirty || busy || halfPair} onClick={onSave}>
          {save.isPending ? '保存中…' : '保存'}
        </button>
        <button type="button" disabled={busy || !s.configured} onClick={() => test.mutate(s.id)} title={s.configured ? '调这家的只读接口（列模型 / 查任务列表），不生成内容、不扣费' : '先填 Key'}>
          {test.isPending ? '测试中…' : '测试连接'}
        </button>
        <span role="status" aria-live="polite" className="small" data-testid={`${id}-result`}>
          {save.error ? (
            <span className="error">{errorText(save.error)}</span>
          ) : test.error ? (
            <span className="error">{errorText(test.error)}</span>
          ) : halfPair ? (
            <span className="error">AccessKey 和 SecretKey 要一起填</span>
          ) : dirty ? (
            <span className="muted">有改动，还没保存</span>
          ) : justSaved ? (
            <span className="saved-text">已保存，马上生效</span>
          ) : result ? (
            <span className={result.ok ? 'saved-text' : 'error'}>{`${result.ok ? '测试通过' : '测试失败'}：${result.message}`}</span>
          ) : null}
        </span>
      </div>
    </div>
  );
}

export function ApiServicesPanel() {
  const q = useApiServices();
  return (
    <FoldPanel
      id="api"
      className="area-api"
      ariaLabel="API 服务"
      title="API 服务"
      defaultOpen={false}
      meta={q.data && `已配置 ${q.data.services.filter((s) => s.configured).length} / ${q.data.services.length}`}
    >
      <p className="muted small">
        用 OpenAI、Anthropic、Google、火山方舟、可灵的官方 API（按量计费）：填了 Key，对应的文本模型就能在上面「默认模型」里选（各步骤也能单独选），
        出图 / 视频引擎（标「API」）能在项目和镜头里选。Key 只存在本机数据库里，页面上只显示掩码；没填时用环境变量。
      </p>
      {q.error && (
        <p className="error" role="alert">
          {errorText(q.error)}
        </p>
      )}
      {!q.data && !q.error && (
        <p className="muted" role="status">
          加载中…
        </p>
      )}
      <div className="api-services">
        {(q.data?.services ?? []).map((s) => (
          <ServiceCard key={s.id} s={s} />
        ))}
      </div>
    </FoldPanel>
  );
}
