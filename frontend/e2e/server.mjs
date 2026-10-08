// 冒烟用的临时后端：临时数据目录 + manual LLM + 不存在的 ComfyUI，生产态托管 frontend/dist。
import { spawn } from 'node:child_process';
import { mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const frontend = resolve(here, '..');
const repo = resolve(frontend, '..');
const port = Number(process.env.E2E_PORT || 8711);
const posix = (p) => p.replace(/\\/g, '/');

// Playwright 在 Windows 上收尾 webServer 时用 taskkill /T /F 硬杀进程树，
// Node 完全没有机会跑退出回调（见 playwright 的 "Graceful shutdown is not
// supported on Windows"），所以 exit 时的清理不可靠。这里改成每次启动先扫一遍，
// 把上一次没清掉的临时目录先删掉，避免越跑越多垃圾。
for (const name of readdirSync(tmpdir())) {
  if (name.startsWith('studio-e2e-')) {
    try {
      rmSync(join(tmpdir(), name), { recursive: true, force: true });
    } catch {
      // 上一轮的目录可能还被占用（极少见），跳过，下次再试
    }
  }
}
const tmp = mkdtempSync(join(tmpdir(), 'studio-e2e-'));

// YAML 是 JSON 的超集，直接写 JSON，省得处理 Windows 路径里的反斜杠转义
const cfg = {
  server: { host: '127.0.0.1', port, data_dir: posix(join(tmp, 'data')) },
  comfyui: { url: 'http://127.0.0.1:9', timeout: 2, max_retries: 1 },
  llm: {
    default: 'manual',
    providers: [{ id: 'manual', kind: 'manual' }],
    routes: { script: 'manual', storyboard: 'manual' },
  },
  log_dir: posix(join(tmp, 'logs')),
  frontend_dist: posix(join(frontend, 'dist')),
};
const cfgPath = join(tmp, 'config.yaml');
writeFileSync(cfgPath, JSON.stringify(cfg, null, 2), 'utf8');
console.log(`[e2e] 临时数据目录 ${tmp}`);

const child = spawn('uv', ['run', 'studio-server'], {
  cwd: repo,
  env: { ...process.env, STUDIO_CONFIG: cfgPath, PYTHONUTF8: '1' },
  stdio: 'inherit',
});
const stop = () => child.kill();
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
child.on('exit', (code) => {
  // Playwright 在测试跑完后会给这个进程发终止信号；清掉临时数据目录，别攒垃圾
  try {
    rmSync(tmp, { recursive: true, force: true });
  } catch {
    // 目录被占用删不掉也不影响退出：下次冒烟会换个新的临时目录
  }
  process.exit(code ?? 0);
});
