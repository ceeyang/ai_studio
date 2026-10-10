// 扩展弹窗：显示和 studio 的连接、各网站登录状态，能改 studio 地址。状态由 background.js 写在 storage.session。
import { DEFAULT_SERVER } from './lib/protocol.js';
import { SITES } from './sites/index.js';

const STATE_TEXT = {
  connected: '已连上 studio',
  connecting: '正在连接 studio…',
  disconnected: '没连上 studio（studio 没启动？会自动重试）',
  error: '连接出错',
};
const PAGE_TEXT = { login_wall: '页面上有登录按钮', logged_in: '页面已登录', unknown: '页面没认出来' };

const $ = (id) => document.getElementById(id);

async function render() {
  const { bridge = {} } = await chrome.storage.session.get('bridge');
  const state = bridge.state || 'connecting';
  $('dot').className = `dot ${state}`;
  $('state').textContent = `${STATE_TEXT[state] || state}${bridge.url ? `（${bridge.url}）` : ''}`;
  $('error').textContent = bridge.error || '';
  const rows = Object.values(SITES).map((spec) => {
    const s = (bridge.sites || {})[spec.key];
    let text = '还没检查';
    if (s) {
      const login = s.logged_in ? '已登录' : '没登录';
      const extra = s.page ? ` · ${PAGE_TEXT[s.page] || s.page}` : '';
      text = `${login} · 开着 ${s.tabs} 个标签页${extra}`;
    }
    return `<tr><td>${spec.label}</td><td class="muted">${text}</td></tr>`;
  });
  $('sites').innerHTML = rows.join('');
}

async function init() {
  const { serverUrl } = await chrome.storage.local.get('serverUrl');
  $('url').value = serverUrl || DEFAULT_SERVER;
  $('save').onclick = async () => {
    const v = $('url').value.trim();
    if (v && !/^wss?:\/\//.test(v)) {
      $('error').textContent = '地址要以 ws:// 开头，例如 ' + DEFAULT_SERVER;
      return;
    }
    await chrome.storage.local.set({ serverUrl: v && v !== DEFAULT_SERVER ? v : '' });
    chrome.runtime.sendMessage({ type: 'reconnect' });
  };
  $('refresh').onclick = () => chrome.runtime.sendMessage({ type: 'status' }).then(render);
  chrome.storage.onChanged.addListener((_c, area) => {
    if (area === 'session') render();
  });
  render();
}

init();
