import { defineConfig, devices } from '@playwright/test';

const PORT = 8711;
// 设了 STUDIO_REAL_URL（如 http://127.0.0.1:8700）就对着已经在跑的真实服务跑 real-*.spec.ts，不起临时服务
const REAL_URL = process.env.STUDIO_REAL_URL;

export default defineConfig({
  testDir: './e2e',
  testMatch: REAL_URL ? ['**/real-*.spec.ts'] : ['**/*.spec.ts'],
  testIgnore: REAL_URL ? [] : ['**/real-*.spec.ts'],
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: REAL_URL ?? `http://127.0.0.1:${PORT}`,
    headless: !REAL_URL,
    trace: 'retain-on-failure',
  },
  webServer: REAL_URL
    ? undefined
    : {
        command: 'node e2e/server.mjs',
        url: `http://127.0.0.1:${PORT}/api/health`,
        timeout: 180_000,
        reuseExistingServer: false,
        stdout: 'pipe',
        stderr: 'pipe',
        env: { E2E_PORT: String(PORT) },
      },
});
