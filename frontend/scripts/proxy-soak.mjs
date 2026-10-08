// 开发代理回归检查：模拟页面轮询 + 突发并发，经 Vite 代理（默认 http://127.0.0.1:5173）打后端，
// 任何 502 / ECONNRESET / 超时都算失败，退出码 1。顺带检查 /api/ws 能经代理连上并保持住。
// 用法（先 .\start.ps1 或分别起后端和 npm run dev）：
//   node scripts/proxy-soak.mjs [baseUrl] [秒数]   （或 npm run proxy-soak -- [baseUrl] [秒数]）
//   node scripts/proxy-soak.mjs http://127.0.0.1:5173 60
// 修复前（http-proxy 默认 Connection: close）：30s 内 57 ok / 130 失败（502 + ECONNRESET）；修复后 60s 387 ok / 0 失败。
import http from 'node:http';

const base = process.argv[2] ?? 'http://127.0.0.1:5173';
const seconds = Number(process.argv[3] ?? 30);
const PATHS = ['/api/queue', '/api/jobs?status=running&limit=5', '/api/projects', '/api/health'];

// 和浏览器一样复用连接，并且在服务端（Vite 默认 5s）关掉空闲连接之前先放掉。
const agent = new http.Agent({ keepAlive: true, maxSockets: 6, timeout: 4000 });
let ok = 0;
const failures = new Map();
const fail = (key) => failures.set(key, (failures.get(key) ?? 0) + 1);

function get(path) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (err) => {
      if (settled) return;
      settled = true;
      if (err) fail(`${err} ${path}`);
      else ok += 1;
      resolve();
    };
    const req = http.get(base + path, { agent }, (res) => {
      res.resume();
      res.on('end', () => done(res.statusCode >= 500 ? `HTTP ${res.statusCode}` : null));
      res.on('error', (e) => done(e.code ?? e.message));
      res.on('close', () => done('response closed early'));
    });
    req.setTimeout(10_000, () => req.destroy(Object.assign(new Error('timeout'), { code: 'TIMEOUT' })));
    req.on('error', (e) => done(e.code ?? e.message));
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function poller(path, everyMs, until) {
  while (Date.now() < until) {
    await get(path);
    await sleep(everyMs);
  }
}

async function bursts(until) {
  while (Date.now() < until) {
    await Promise.all(Array.from({ length: 16 }, (_, i) => get(PATHS[i % PATHS.length])));
    await sleep(500 + Math.random() * 6000); // 跨过 5s 左右的空闲超时边界
  }
}

async function wsCheck(until) {
  const url = base.replace(/^http/, 'ws') + '/api/ws';
  return new Promise((resolve) => {
    const ws = new WebSocket(url);
    let opened = false;
    let closing = false;
    const timer = setTimeout(() => { closing = true; ws.close(); }, Math.max(0, until - Date.now()));
    ws.onopen = () => { opened = true; };
    // 只统计连接期间的错误；我们主动关闭时的收尾报错见 vite.config.ts 里的说明，不算代理失败。
    ws.onerror = () => { if (!closing) fail(`ws error ${url}`); };
    ws.onclose = () => {
      clearTimeout(timer);
      if (!opened) fail(`ws never opened ${url}`);
      else if (!closing) fail(`ws closed early ${url}`);
      resolve();
    };
  });
}

const until = Date.now() + seconds * 1000;
await Promise.all([
  poller('/api/queue', 1000, until),
  poller('/api/jobs?status=running&limit=5', 5000, until),
  poller('/api/projects', 5000, until),
  bursts(until),
  wsCheck(until),
]);
agent.destroy();

const bad = [...failures.values()].reduce((a, b) => a + b, 0);
console.log(`proxy soak ${base} ${seconds}s: ${ok} ok, ${bad} failed`);
for (const [k, v] of failures) console.log(`  ${v} x ${k}`);
process.exit(bad === 0 ? 0 : 1);
