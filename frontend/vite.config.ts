import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// 开发态：页面在 127.0.0.1:5173，/api（含 WebSocket）和 /media 转给后端。
// 后端只认 127.0.0.1 / localhost 的 Host 和 Origin，这里两边都是 127.0.0.1，不用改写。
const BACKEND = 'http://127.0.0.1:8700';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND, ws: true },
      '/media': { target: BACKEND },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
});
