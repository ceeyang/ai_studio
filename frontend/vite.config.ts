import { Agent } from 'node:http';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// 开发态：页面在 127.0.0.1:5173，/api 和 /media 转给后端。
// 后端只认 127.0.0.1 / localhost 的 Host 和 Origin，这里两边都是 127.0.0.1，不用改写。
// WebSocket（/api/ws）不走这个代理：浏览器直连后端，见 src/ws/useStudioEvents.ts 里的说明。
const BACKEND = 'http://127.0.0.1:8700';

// 代理到后端的 HTTP 请求必须复用长连接。http-proxy 默认不用 agent，会给每个请求加 Connection: close，
// 于是 uvicorn 回完响应立刻关连接；在装了 WFP 网络过滤驱动（火绒、AdGuard 等）的 Windows 上，
// 这种“写完马上关”的回环连接经常被打成 RST，响应还没读到就丢了，Vite 报 read ECONNRESET、页面拿到 502。
// 用长连接后关连接的一方变成这里：空闲 4s 就回收，早于 uvicorn 默认的 5s keep-alive 超时，
// 不会拿到后端刚关掉的连接去发请求。
export const backendAgent = new Agent({ keepAlive: true, timeout: 4000 });

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, agent: backendAgent },
      '/media': { target: BACKEND, agent: backendAgent },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
