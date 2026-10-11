import { useState } from 'react';
import { FoldPanel } from './FoldPanel';
import { errorText } from '../../api/client';
import { useAppSettings, useBrowserStatus, useOpenBrowserLogin, useSaveSettings } from '../../api/queries';
import type { BrowserSiteStatus, BrowserStatus, WebRegion } from '../../api/types';
import { engineLabel } from '../../format';

const PAGE_TEXT: Record<string, string> = {
  login_wall: '页面上有登录按钮',
  logged_in: '页面已登录',
  unknown: '页面没认出来',
};

const FEATURE_TEXT: Record<string, string> = {
  rpc: '网页文本模型（ChatGPT / Gemini / Claude / Muse 网页）',
  video: '网页出视频（Gemini / Muse）',
  generator: '即梦 / 可灵网页出视频',
};

const REGION_SITES: { key: 'web_region_jimeng' | 'web_region_kling'; label: string; cn: string; global: string }[] = [
  { key: 'web_region_jimeng', label: '即梦区域', cn: '国内（即梦 jimeng.jianying.com）', global: '海外（Dreamina dreamina.capcut.com）' },
  { key: 'web_region_kling', label: '可灵区域', cn: '国内（klingai.com）', global: '海外（kling.ai）' },
];

/** 老扩展缺的能力 → 「网页出视频（Gemini / Muse）、同一集接着用之前的对话」；后端没给就按最老的说 */
export function missingText(missing: string[] | undefined): string {
  const list = (missing?.length ? missing : ['rpc']).map((f) => FEATURE_TEXT[f] ?? f);
  return list.join('、');
}

function loginText(v: boolean | null | undefined): [string, 'ok' | 'warn' | 'fail'] {
  if (v === true) return ['已登录', 'ok'];
  if (v === false) return ['没登录', 'fail'];
  return ['未知', 'warn'];
}

function StatusIcon({ status }: { status: 'ok' | 'warn' | 'fail' }) {
  return <span className={`status-icon status-${status}`}>{status === 'ok' ? '√' : status === 'warn' ? '!' : '×'}</span>;
}

function InstallSteps({ dir }: { dir: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard?.writeText(dir).then(() => setCopied(true), () => setCopied(false));
  };
  return (
    <ol className="install-steps" data-testid="extension-install-steps">
      <li>
        在你平时用的 Chrome 地址栏打开 <code>chrome://extensions</code>（Edge 是 <code>edge://extensions</code>）。
      </li>
      <li>打开「开发者模式」（Chrome 在右上角，Edge 在左侧栏）。</li>
      <li>
        点「加载已解压的扩展程序」，选择文件夹 <code data-testid="extension-dir">{dir}</code>{' '}
        <button type="button" className="link" onClick={copy}>
          {copied ? '已复制' : '复制路径'}
        </button>
      </li>
      <li>
        在这个浏览器里登录要用的网站：ChatGPT（chatgpt.com）、Gemini（gemini.google.com）、Muse（muse.ai，Meta 账号），只需一次；要用 Claude 网页写文本的再登录 Claude（claude.ai）；要用即梦 / 可灵网页出视频的登录即梦（jimeng.jianying.com）/ 可灵（klingai.com），海外站见下面的区域设置。
      </li>
      <li>保持浏览器开着。扩展会自动连上 studio（{window.location.host}），这里会显示「扩展已连上」。</li>
    </ol>
  );
}

function SiteRows({ sites }: { sites: Record<string, BrowserSiteStatus> }) {
  return (
    <table className="table browser-sites">
      <thead>
        <tr>
          <th />
          <th>网站</th>
          <th>登录</th>
          <th>标签页</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(sites).map(([key, s]) => {
          const [text, st] = loginText(s.logged_in);
          return (
            <tr key={key} data-testid={`browser-site-${key}`}>
              <td>
                <StatusIcon status={st} />
              </td>
              <td>
                {s.label} <span className="muted">{s.host}</span>
              </td>
              <td>
                {text}
                {s.page && <span className="muted"> · {PAGE_TEXT[s.page] ?? s.page}</span>}
              </td>
              <td>{s.tabs == null ? '—' : s.tabs === 0 ? '没开（出图时在后台新开）' : `${s.tabs} 个`}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function ExtensionBody({ st }: { st: BrowserStatus }) {
  const login = useOpenBrowserLogin();
  const ext = st.extension;
  const connected = !!st.connected;
  return (
    <>
      <p className="browser-conn" data-testid="extension-status">
        <StatusIcon status={connected ? 'ok' : 'warn'} />{' '}
        {connected
          ? `扩展已连上 · v${ext?.version ?? '?'} · ${ext?.browser ?? '浏览器'}`
          : `扩展没有连上：${engineLabel('gpt_image')} / ${engineLabel('gemini_image')}出图会失败`}
      </p>
      <p className="muted">
        出图、出视频在你平时用的浏览器里进行：扩展在已登录的 ChatGPT / Gemini / Muse 标签页里打开对话（同一集的任务接着用同一个对话）、传参考图或首帧、输提示词，出完把原图 / 视频传回来。studio
        不启动浏览器，也不读取 cookie。
      </p>
      {connected && st.needs_reload && (
        <p className="error" role="alert" data-testid="extension-needs-reload">
          扩展需要重新加载：现在连着的是 v{ext?.version ?? '?'}，还不支持{missingText(st.missing_features)}。在{' '}
          <code>chrome://extensions</code>（Edge 是 <code>edge://extensions</code>）里找到「AI Studio 网页出图桥」点「重新加载」。出图不受影响。
        </p>
      )}
      <p className="muted">Claude 网页只用来写文本（网页文本模型），不出图。</p>
      {connected && st.sites && <SiteRows sites={st.sites} />}
      {connected && (
        <div className="row">
          <button type="button" disabled={login.isPending} onClick={() => login.mutate()}>
            {login.isPending ? '正在打开…' : '在浏览器里打开 ChatGPT / Gemini / Muse / Claude'}
          </button>
        </div>
      )}
      {login.data && (
        <p className="notice" data-testid="browser-login-notice">
          已在你的浏览器里打开 ChatGPT、Gemini、Muse 和 Claude：没登录的就在那里登录，登录后这里会自动刷新。
        </p>
      )}
      {login.error && <p className="error">打开失败：{errorText(login.error)}</p>}
      {connected ? (
        <details>
          <summary>安装步骤</summary>
          <InstallSteps dir={st.extension_dir ?? 'browser-extension'} />
        </details>
      ) : (
        <InstallSteps dir={st.extension_dir ?? 'browser-extension'} />
      )}
    </>
  );
}

function CdpBody({ st }: { st: BrowserStatus }) {
  const login = useOpenBrowserLogin();
  return (
    <>
      <p className="browser-conn" data-testid="cdp-status">
        <StatusIcon status={st.running ? 'ok' : 'warn'} /> {st.running ? `studio 的浏览器在运行（${st.endpoint}）` : 'studio 的浏览器没在运行，出图时自动启动'}
      </p>
      <div className="row">
        <button
          type="button"
          disabled={login.isPending}
          title="打开 studio 专用的浏览器窗口（独立配置目录），在里面登录 ChatGPT 和 Gemini，只需一次"
          onClick={() => login.mutate()}
        >
          {login.isPending ? '正在打开浏览器…' : '打开浏览器登录'}
        </button>
      </div>
      {login.data && (
        <p className="notice" data-testid="browser-login-notice">
          {login.data.launched ? '已启动浏览器' : '浏览器已在运行，已新开标签页'}：请在弹出的窗口里登录 ChatGPT 和 Gemini（只需一次，登录态保存在{' '}
          {login.data.user_data_dir}），登录后点「重新检查」。
        </p>
      )}
      {login.error && <p className="error">打开浏览器失败：{errorText(login.error)}</p>}
    </>
  );
}

/** 全局开关「重新生成时续接之前的网页会话」：读写 /api/settings 的 web_reuse_conversation（老后端没有这一项时按开着算） */
function ReuseConversation({ hint }: { hint?: string | null }) {
  const settings = useAppSettings();
  const save = useSaveSettings();
  if (!settings.data) return null;
  const on = settings.data.web_reuse_conversation ?? true;
  return (
    <div className="web-reuse" data-testid="web-reuse-conversation">
      <label className="check">
        <input
          type="checkbox"
          checked={on}
          disabled={save.isPending}
          onChange={(e) => save.mutate({ web_reuse_conversation: e.target.checked })}
        />{' '}
        重新生成时续接之前的网页会话
      </label>
      <p className="muted small">
        同一角色 / 镜头 / 场景 / 道具在同一网站上重新出图、以及剧本 / 分镜用网页文本模型时，接着上次的对话发（网页里能看到前后几版）；超过
        20 轮自动新开。关掉后每次都新开对话；单次不想续接，在出图的地方勾「新开对话」。
      </p>
      {hint && <p className="muted small" data-testid="web-reuse-hint">{hint}</p>}
      {save.error && <p className="error small">保存失败：{errorText(save.error)}</p>}
    </div>
  );
}

/** 即梦 / 可灵网页出视频用国内站还是海外站：读写 /api/settings 的 web_region_jimeng / web_region_kling
 *  （老后端没有这两项时不显示） */
function WebRegions() {
  const settings = useAppSettings();
  const save = useSaveSettings();
  const d = settings.data;
  if (!d || (d.web_region_jimeng === undefined && d.web_region_kling === undefined)) return null;
  return (
    <div className="web-regions" data-testid="web-regions">
      {REGION_SITES.map((s) => (
        <label key={s.key} className="row">
          {s.label}{' '}
          <select
            aria-label={s.label}
            value={d[s.key] ?? 'cn'}
            disabled={save.isPending}
            onChange={(e) => save.mutate({ [s.key]: e.target.value as WebRegion })}
          >
            <option value="cn">{s.cn}</option>
            <option value="global">{s.global}</option>
          </select>
        </label>
      ))}
      <p className="muted small">
        即梦 / 可灵网页出视频打开哪个站点（在装了扩展的浏览器里登录对应的站）。默认国内；海外站要用海外账号。
      </p>
      {save.error && <p className="error small">保存失败：{errorText(save.error)}</p>}
    </div>
  );
}

/** 设置页「网页出图」：扩展方式显示连接、各网站登录和安装步骤；cdp 方式保留「打开浏览器登录」。 */
export function BrowserPanel() {
  const status = useBrowserStatus();
  const st = status.data;
  return (
    <FoldPanel
      id="browser"
      className="area-browser"
      testId="browser-panel"
      title={`网页出图（${st?.transport === 'cdp' ? 'studio 管的浏览器' : '浏览器扩展'}）`}
    >
      {status.error && <p className="error">读浏览器状态失败：{errorText(status.error)}</p>}
      {!st && !status.error && <p className="muted">读取中…</p>}
      {st?.transport === 'extension' && <ExtensionBody st={st} />}
      {st?.transport === 'cdp' && <CdpBody st={st} />}
      <ReuseConversation hint={st?.conversation && !st.conversation.supported ? st.conversation.hint : null} />
      <WebRegions />
    </FoldPanel>
  );
}
