import { act, cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { closeMediaViewer } from '../components/MediaViewer';

afterEach(() => {
  cleanup();
  act(() => closeMediaViewer());
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});
