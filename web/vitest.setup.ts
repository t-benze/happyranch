import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from './src/test/server';

// Node's experimental storage globals can be present-but-undefined. Bind the
// JSDOM origin-backed implementations so window and global test code share one
// storage realm.
let storageFrame: HTMLIFrameElement | undefined;
if (typeof document !== 'undefined') {
  storageFrame = document.createElement('iframe');
  document.body.append(storageFrame);
  const storageWindow = storageFrame.contentWindow;
  if (!storageWindow) {
    throw new Error('JSDOM storage window is unavailable');
  }

  const { localStorage, sessionStorage } = storageWindow;
  Object.defineProperties(globalThis, {
    localStorage: { configurable: true, value: localStorage },
    sessionStorage: { configurable: true, value: sessionStorage },
  });
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  globalThis.sessionStorage?.clear();
});
afterAll(() => {
  server.close();
  storageFrame?.remove();
});
