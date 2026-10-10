import { useState } from 'react';
import { errorText } from '../../api/client';
import { useBrowserStatus, useOpenBrowserLogin } from '../../api/queries';
import type { BrowserSiteStatus, BrowserStatus } from '../../api/types';
import { engineLabel } from '../../format';

const PAGE_TEXT: Record<string, string> = {
  login_wall: '页面上有登录按钮',
  logged_in: '页面已登录',
  unknown: '页面没认出来',
};

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
      <li>在这个浏览器里登录 ChatGPT（chatgpt.com）和 Gemini（gemini.google.com），只需一次。</li>
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
        出图在你平时用的浏览器里进行：扩展在已登录的 ChatGPT / Gemini 标签页里开新对话、传参考图、输提示词，出完把原图传回来。studio
        不启动浏览器，也不读取 cookie。
      </p>
      {connected && st.sites && <SiteRows sites={st.sites} />}
      {connected && (
        <div className="row">
          <button type="button" disabled={login.isPending} onClick={() => login.mutate()}>
            {login.isPending ? '正在打开…' : '在浏览器里打开 ChatGPT / Gemini'}
          </button>
        </div>
      )}
      {login.data && (
        <p className="notice" data-testid="browser-login-notice">
          已在你的浏览器里打开 ChatGPT 和 Gemini：没登录的就在那里登录，登录后这里会自动刷新。
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

/** 设置页「网页出图」：扩展方式显示连接、各网站登录和安装步骤；cdp 方式保留「打开浏览器登录」。 */
export function BrowserPanel() {
  const status = useBrowserStatus();
  const st = status.data;
  return (
    <section className="panel area-browser" data-testid="browser-panel">
      <h2>网页出图（{st?.transport === 'cdp' ? 'studio 管的浏览器' : '浏览器扩展'}）</h2>
      {status.error && <p className="error">读浏览器状态失败：{errorText(status.error)}</p>}
      {!st && !status.error && <p className="muted">读取中…</p>}
      {st?.transport === 'extension' && <ExtensionBody st={st} />}
      {st?.transport === 'cdp' && <CdpBody st={st} />}
    </section>
  );
}
