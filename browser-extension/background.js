/*
 * AI Studio 网页出图桥（service worker）。
 *
 * 1. 用 WebSocket 连本机 studio（默认 ws://127.0.0.1:8700/api/browser/ext，弹窗里能改），断了按 1s、2s、4s …
 *    最多 30s 重连；每 20 秒发一次 ping（MV3 的 service worker 空闲 30 秒会被挂起，有收发就不会），
 *    chrome.alarms 每 30 秒兜底一次：被挂起后醒来重连。
 * 2. studio 发来出图任务：在这个浏览器里找一个 ChatGPT / Gemini 标签页（上次出图用过的 → 你开着、没在看、
 *    没在生成、输入框里没写东西的 → 都没有就在后台新开一个），切到新对话，按 lib/flow.js 走完出图流程，
 *    把原图分块传回 studio。每一步页面操作都是一次 chrome.scripting.executeScript（content/dom.js），
 *    等待和轮询在这里做：后台标签页里的定时器会被浏览器节流，service worker 里的不会。
 * 3. 报状态：每个网站开着几个标签页、登录 cookie 在不在（chrome.cookies，只看名字不读值）、开着的标签页上
 *    有没有登录按钮。
 * 4. 出视频（v0.4 起）：job.kind = 'video' 时在 Gemini / Muse 上用首帧出视频，原视频（mp4）同样分块传回。
 *    续接会话（v0.3 起）：出图任务带 conversation_url 时在那条之前的对话里接着出图（打不开就新开），
 *    结果里报回对话地址，studio 按角色 / 镜头 / 场景 / 道具记下来，下次重新生成时续接。
 * 5. rpc（v0.2 起，网页文本模型）：studio 一步步驱动页面——要一个标签页（v0.4.1 起和出图共用，尽量不新开）、
 *    跳转、调 content/dom.js 的函数（只许 lib/protocol.js 的 RPC_DOM_OPS 里那些）。流程和选择器都在 studio
 *    那边（backend/engines/browser/chat.py），改了不用重新加载扩展。
 *
 * 不读、不拷、不上传任何 cookie 的值；只在 ChatGPT / Gemini / Muse / Claude 的页面里操作。
 */
import { Canceled, FlowError, generate } from './lib/flow.js';
import {
  DEFAULT_SERVER, FEATURES, PING_SECONDS, REPLACED_CODE, backoffMs, bufferToB64, imageMessages, loggedIn, onSite,
  rankTabs, rpcProblem,
} from './lib/protocol.js';
import { IMAGE_SITES, SITES, VIDEO_SITES } from './sites/index.js';

const VERSION = chrome.runtime.getManifest().version;
const ALARM = 'ai-studio-keepalive';

let ws = null;
let attempt = 0;
let pingTimer = null;
let reconnectTimer = null;
let options = {};
let statusTimer = null;
let alarmTicks = 0;
const jobs = new Map(); // job_id → {canceled}
const siteChains = {}; // 每个网站的任务串行执行

const sleep = (s) => new Promise((r) => setTimeout(r, s * 1000));

// ---------------------------------------------------------------- 状态（给弹窗看）
async function setState(patch) {
  try {
    const cur = (await chrome.storage.session.get('bridge')).bridge || {};
    await chrome.storage.session.set({ bridge: { ...cur, ...patch, at: Date.now() } });
  } catch (_e) {
    /* 弹窗看不到最新状态而已 */
  }
}

function browserName() {
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const b = brands.find((x) => /Microsoft Edge|Google Chrome|Brave|Opera/.test(x.brand)) ||
    brands.find((x) => /Chromium/.test(x.brand));
  return b ? `${b.brand} ${b.version}` : navigator.userAgent;
}

async function serverUrl() {
  try {
    const { serverUrl: u } = await chrome.storage.local.get('serverUrl');
    return u || DEFAULT_SERVER;
  } catch (_e) {
    return DEFAULT_SERVER;
  }
}

// ---------------------------------------------------------------- WebSocket
function isOpen() {
  return ws && ws.readyState === WebSocket.OPEN;
}

function send(msg) {
  if (!isOpen()) return false;
  ws.send(JSON.stringify(msg));
  return true;
}

function scheduleReconnect(ms) {
  if (reconnectTimer) return;
  const wait = ms != null ? ms : backoffMs(attempt++);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, wait);
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  const url = await serverUrl();
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  let sock;
  try {
    sock = new WebSocket(url);
  } catch (e) {
    await setState({ state: 'error', url, error: `地址不对：${e.message || e}` });
    scheduleReconnect();
    return;
  }
  ws = sock;
  await setState({ state: 'connecting', url });
  sock.onopen = () => {
    attempt = 0;
    send({
      type: 'hello', version: VERSION, sites: Object.keys(SITES), features: FEATURES,
      browser: browserName(), ext_id: chrome.runtime.id,
    });
    setState({ state: 'connected', url, error: '' });
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: 'ping', t: Date.now() }), PING_SECONDS * 1000);
    reportStatus();
  };
  sock.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (_e) {
      return;
    }
    onMessage(msg);
  };
  sock.onclose = (ev) => {
    if (ws !== sock) return;
    ws = null;
    clearInterval(pingTimer);
    pingTimer = null;
    // 连接没了，结果也送不回去了：正在跑的任务停掉（studio 那边已经判失败）
    for (const j of jobs.values()) j.canceled = true;
    const replaced = ev.code === REPLACED_CODE;
    setState({
      state: 'disconnected', url, code: ev.code,
      error: replaced ? '另一个浏览器（或重新加载后的扩展）连上了 studio，这个先让开，1 分钟后再试' : '',
    });
    scheduleReconnect(replaced ? 60000 : null);
  };
  sock.onerror = () => {
    /* 后面一定跟着 onclose */
  };
}

function reconnectNow() {
  attempt = 0;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    const old = ws;
    ws = null;
    try {
      old.close();
    } catch (_e) {
      /* 已经关了 */
    }
  }
  connect();
}

function onMessage(msg) {
  switch (msg.type) {
    case 'welcome':
      options = msg.options || {};
      break;
    case 'job':
      runJob(msg);
      break;
    case 'rpc':
      handleRpc(msg);
      break;
    case 'cancel': {
      const j = jobs.get(msg.job_id);
      if (j) j.canceled = true;
      break;
    }
    case 'status_request':
      reportStatusSoon(0);
      break;
    case 'open':
      openUrls(msg.urls || []);
      break;
    default:
      break;
  }
}

async function openUrls(urls) {
  for (const url of urls) {
    if (!/^https:\/\//.test(url)) continue;
    await chrome.tabs.create({ url, active: true });
  }
  send({ type: 'opened', urls });
}

// ---------------------------------------------------------------- 页面操作
/** 注入到页面里执行的函数（会被序列化，不能用外面的变量）。 */
function callDom(name, args) {
  const D = globalThis.__aiStudioDom;
  if (!D) return { __missing: true };
  return D[name](...args);
}

async function exec(tabId, name, args) {
  for (let i = 0; i < 4; i++) {
    let res;
    try {
      [res] = await chrome.scripting.executeScript({ target: { tabId }, func: callDom, args: [name, args] });
    } catch (e) {
      if (/No tab with id/i.test(String(e && e.message))) {
        throw new FlowError('browser', '出图用的标签页被关掉了，重试这个任务');
      }
      await sleep(0.3); // 页面正在跳转、还不能注入
      continue;
    }
    const r = res ? res.result : undefined;
    if (r && r.__missing) {
      try {
        await chrome.scripting.executeScript({ target: { tabId }, files: ['content/dom.js'] });
      } catch (_e) {
        await sleep(0.3);
      }
      continue;
    }
    return r;
  }
  return undefined;
}

async function probeTab(tabId, spec) {
  const p = await exec(tabId, 'probe', [{ prompt_box: spec.prompt_box, login_wall: spec.login_wall, stop_button: spec.stop_button }]);
  return p && typeof p === 'object' ? p : null;
}

async function waitTabComplete(tabId, timeoutS = 60) {
  const end = Date.now() + timeoutS * 1000;
  await sleep(0.5);
  while (Date.now() < end) {
    let t;
    try {
      t = await chrome.tabs.get(tabId);
    } catch (_e) {
      throw new FlowError('browser', '出图用的标签页被关掉了，重试这个任务');
    }
    if (t.status === 'complete') return;
    await sleep(0.3);
  }
}

/** flow.js 用的 driver：每个方法一次 executeScript。找不到元素时返回 false / 0 / '' / []。 */
class TabDriver {
  constructor(tabId) {
    this.tabId = tabId;
  }
  op(name, ...args) {
    return exec(this.tabId, name, args);
  }
  async goto(url) {
    await chrome.tabs.update(this.tabId, { url });
    await waitTabComplete(this.tabId);
  }
  async url() {
    try {
      return (await chrome.tabs.get(this.tabId)).url || '';
    } catch (_e) {
      return '';
    }
  }
  async visible(sel) { return !!(await this.op('visible', sel)); }
  async count(sel) { return Number(await this.op('count', sel)) || 0; }
  async enabled(sel) { return !!(await this.op('enabled', sel)); }
  async click(sel) { return !!(await this.op('click', sel)); }
  async typeText(sel, text) { return !!(await this.op('typeText', sel, text)); }
  async press(sel, key) { return !!(await this.op('press', sel, key)); }
  async setFiles(sel, files, near) { return !!(await this.op('setFiles', sel, files, near)); }
  async fileInputs(sel, near) { return (await this.op('fileInputs', sel, near)) || { total: 0, near: 0 }; }
  async composerImages(near) { return Number(await this.op('composerImages', near)) || 0; }
  async pasteFiles(sel, files) { return !!(await this.op('pasteFiles', sel, files)); }
  async dropFiles(sel, files) { return !!(await this.op('dropFiles', sel, files)); }
  async text(sel, exclude) { const t = await this.op('text', sel, exclude); return typeof t === 'string' ? t : ''; }
  async images(sel) { const r = await this.op('images', sel); return Array.isArray(r) ? r : []; }
  async videos(sel) { const r = await this.op('videos', sel); return Array.isArray(r) ? r : []; }
  async cookieNames(url) {
    try {
      return (await chrome.cookies.getAll({ url })).map((c) => c.name);
    } catch (_e) {
      return [];
    }
  }
  sleep(s) {
    return sleep(s);
  }
  /** 先在页面里取（同源带登录态、blob: 也行）；不行再由扩展直接取（有 host 权限，不受 CORS 限制）。 */
  async fetchImage(src) {
    if (src.startsWith('data:')) return { mime: '', b64: src.slice(src.indexOf(',') + 1), via: 'page' };
    const r = await this.op('fetchB64', src);
    if (r && r.b64) return { mime: r.mime, b64: r.b64, via: 'page' };
    if (src.startsWith('blob:')) throw new Error(`页面里取 blob 图失败：${(r && r.error) || '?'}`);
    const resp = await fetch(src, { credentials: 'include' });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}（页面里取：${(r && r.error) || '?'}）`);
    return { mime: resp.headers.get('content-type') || '', b64: bufferToB64(await resp.arrayBuffer()), via: 'extension' };
  }
}

// ---------------------------------------------------------------- 标签页
async function studioTabs() {
  try {
    return (await chrome.storage.session.get('studioTabs')).studioTabs || {};
  } catch (_e) {
    return {};
  }
}

async function rememberTab(key, tabId) {
  const cur = await studioTabs();
  cur[key] = tabId;
  try {
    await chrome.storage.session.set({ studioTabs: cur });
  } catch (_e) {
    /* 下次找不到就新开一个 */
  }
}

/**
 * 出图（purpose = image）、写文本（text）用的标签页。两种用途共用：先用扩展上次用过的（这次用途的、另一种用途的），
 * 再借你开着、没在看、没在生成、输入框里没写东西的这个网站的标签页（会切到新对话），只有这个网站一个能用的标签页
 * 都没有时才在后台新开一个。选中的标签页同时记成两种用途的，下次两边都先用它。
 */
async function acquireSiteTab(spec, opts, purpose) {
  const all = await studioTabs();
  const slots = purpose === 'text' ? [`${spec.key}:text`, spec.key] : [spec.key, `${spec.key}:text`];
  const ids = slots.map((k) => all[k]).filter((x) => x != null);
  const tabs = await chrome.tabs.query({});
  const ranked = rankTabs(tabs, spec, { studioTabIds: ids, reuseUserTabs: opts.reuse_user_tabs !== false });
  const remember = async (id) => {
    for (const k of slots) await rememberTab(k, id);
  };
  for (const t of ranked) {
    const isMine = ids.includes(t.id);
    if (!t.discarded && t.status === 'complete') {
      const p = await probeTab(t.id, spec);
      if (!p) continue; // 注入不了（比如停在别的域名的登录页上）
      if (p.busy) continue; // 正在生成
      if (!isMine && p.draft) continue; // 你在输入框里写了一半
    }
    await remember(t.id);
    return {
      tabId: t.id, fresh: false,
      how: isMine ? `用上次 studio 用过的那个 ${spec.label} 标签页` : `借用你开着的一个 ${spec.label} 标签页（会切到新对话）`,
    };
  }
  const tab = await chrome.tabs.create({ url: spec.new_chat_url, active: !!opts.activate_tab });
  try {
    await chrome.tabs.update(tab.id, { autoDiscardable: false });
  } catch (_e) {
    /* 老版本浏览器没有这个选项 */
  }
  await remember(tab.id);
  await waitTabComplete(tab.id);
  return { tabId: tab.id, fresh: true, how: `没有能用的 ${spec.label} 标签页（都在用或没开），在后台新开了一个` };
}

function acquireTab(spec, opts) {
  return acquireSiteTab(spec, opts, 'image');
}

// ---------------------------------------------------------------- rpc（网页文本模型）
function textTabIds(all) {
  return Object.entries(all).filter(([k]) => k.endsWith(':text')).map(([, v]) => v);
}

/** 写文本用的标签页：和出图共用（见 acquireSiteTab），尽量不新开。 */
async function acquireTextTab(spec, opts) {
  const { tabId, fresh, how } = await acquireSiteTab(spec, opts, 'text');
  return { tab_id: tabId, fresh, how };
}

const RPC = {
  acquire_tab: (p) => acquireTextTab(SITES[p.site], { ...options, ...(p.options || {}) }),
  async goto(p) {
    await chrome.tabs.update(p.tab_id, { url: p.url });
    await waitTabComplete(p.tab_id);
    return (await chrome.tabs.get(p.tab_id)).url || '';
  },
  async url(p) {
    try {
      return (await chrome.tabs.get(p.tab_id)).url || '';
    } catch (_e) {
      throw new FlowError('browser', '写文本用的标签页被关掉了，重试一次');
    }
  },
  async dom(p) {
    const r = await exec(p.tab_id, p.op, Array.isArray(p.args) ? p.args : []);
    return r === undefined ? null : r;
  },
  async cookie_names(p) {
    try {
      return (await chrome.cookies.getAll({ url: p.url })).map((c) => c.name);
    } catch (_e) {
      return [];
    }
  },
};

async function handleRpc(msg) {
  const id = msg.id;
  const method = String(msg.method || '');
  const params = msg.params || {};
  const reply = (patch) => send({ type: 'rpc_result', id, ...patch });
  try {
    const tabs = new Set(textTabIds(await studioTabs()));
    const why = rpcProblem(method, params, { tabs, sites: SITES });
    if (why) {
      reply({ ok: false, kind: 'browser', error: why });
      return;
    }
    reply({ ok: true, result: await RPC[method](params) });
  } catch (e) {
    const kind = e instanceof FlowError ? e.kind : 'browser';
    reply({ ok: false, kind, error: String((e && e.message) || e) });
  }
}

// ---------------------------------------------------------------- 出图任务
function runJob(msg) {
  const spec = SITES[msg.site];
  const video = msg.kind === 'video';
  const allowed = video ? VIDEO_SITES : IMAGE_SITES;
  if (!spec || !allowed.includes(spec.key)) {
    send({
      type: 'error', job_id: msg.job_id, kind: 'browser',
      message: `扩展不能在网站 ${msg.site} 上${video ? '出视频' : '出图'}（支持 ${allowed.join('、')}）：` +
        '扩展版本太旧的话，在扩展管理页重新加载一下',
    });
    return;
  }
  const job = { canceled: false };
  jobs.set(msg.job_id, job);
  const prev = siteChains[spec.key] || Promise.resolve();
  siteChains[spec.key] = prev
    .catch(() => {})
    .then(() => doJob(msg, spec, job))
    .finally(() => jobs.delete(msg.job_id));
}

async function doJob(msg, spec, job) {
  const id = msg.job_id;
  const opts = { ...options, ...(msg.options || {}) };
  const log = (m) => send({ type: 'log', job_id: id, message: m });
  const progress = (v, m) => send({ type: 'progress', job_id: id, value: v, max: m });
  try {
    if (job.canceled) return;
    const { tabId, fresh, how } = await acquireTab(spec, opts);
    log(`${spec.label}：${how}`);
    if (opts.activate_tab) await chrome.tabs.update(tabId, { active: true });
    let conversation = null;
    const images = await generate(
      new TabDriver(tabId), spec,
      {
        kind: msg.kind === 'video' ? 'video' : 'image', aspect: msg.aspect,
        prompt: msg.prompt, refs: msg.refs || [], timeout: msg.timeout, reuseConversation: !!opts.reuse_conversation, fresh,
        conversationUrl: msg.conversation_url || '',
      },
      { isCanceled: () => job.canceled, log, progress, poll: 1, onConversation: (c) => { conversation = c; } },
    );
    images.forEach((img, i) => imageMessages(id, i, img).forEach(send));
    // v0.3 起：报回对话地址（studio 按角色 / 镜头等记下来，下次重新生成时续接）
    send({ type: 'result', job_id: id, count: images.length, conversation });
  } catch (e) {
    if (e instanceof Canceled) return;
    const kind = e instanceof FlowError ? e.kind : 'browser';
    const message = e instanceof FlowError ? e.message : `${spec.label}：扩展出错了（${(e && e.message) || e}）`;
    send({ type: 'error', job_id: id, kind, message });
  } finally {
    reportStatusSoon(1000);
  }
}

// ---------------------------------------------------------------- 状态
async function reportStatus() {
  if (!isOpen()) return;
  const tabs = await chrome.tabs.query({});
  const mine = await studioTabs();
  const sites = {};
  for (const spec of Object.values(SITES)) {
    const siteTabs = tabs.filter((t) => t.url && onSite(t.url, spec));
    let cookie = null;
    try {
      const cs = await chrome.cookies.getAll({ url: spec.cookie_url });
      cookie = cs.some((c) => spec.session_cookies.includes(c.name));
    } catch (_e) {
      /* 没有 cookies 权限时只看页面 */
    }
    let page = null;
    const ready = siteTabs.filter((t) => t.status === 'complete' && !t.discarded);
    const t = ready.find((x) => x.id === mine[spec.key]) || ready[0];
    if (t) {
      const p = await probeTab(t.id, spec);
      if (p) page = p.loginWall ? 'login_wall' : p.prompt ? 'logged_in' : 'unknown';
    }
    sites[spec.key] = { tabs: siteTabs.length, cookie, page, logged_in: loggedIn(cookie, page) };
  }
  send({ type: 'status', sites });
  setState({ sites });
}

function reportStatusSoon(ms = 2000) {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    reportStatus().catch(() => {});
  }, ms);
}

// ---------------------------------------------------------------- 事件
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name !== ALARM) return;
  if (!isOpen()) {
    connect();
    return;
  }
  send({ type: 'ping', t: Date.now() });
  if (++alarmTicks % 2 === 0) reportStatusSoon(0);
});

function ensureAlarm() {
  chrome.alarms.get(ALARM, (a) => {
    if (!a) chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
  });
}

chrome.runtime.onInstalled.addListener(() => {
  ensureAlarm();
  connect();
});
chrome.runtime.onStartup.addListener(() => {
  ensureAlarm();
  connect();
});

chrome.tabs.onUpdated.addListener((_id, info, tab) => {
  if (info.status === 'complete' && tab.url && Object.values(SITES).some((s) => onSite(tab.url, s))) reportStatusSoon();
});
chrome.tabs.onRemoved.addListener(() => reportStatusSoon());

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.serverUrl) reconnectNow();
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === 'reconnect') {
    reconnectNow();
    reply({ ok: true });
  } else if (msg && msg.type === 'status') {
    reportStatus().finally(() => reply({ ok: true }));
    return true;
  }
  return false;
});

// service worker 每次被唤醒（任何事件）都会重新执行到这里
ensureAlarm();
connect();
