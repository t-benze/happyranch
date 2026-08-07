import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from './src/test/server';

// Node's experimental storage globals can be present-but-undefined. Bind the
// JSDOM origin-backed implementations so window and global test code share one
// storage realm.
const jsdomWindow = window as Window & {
  _localStorage: Storage;
  _sessionStorage: Storage;
};
Object.defineProperties(globalThis, {
  localStorage: { configurable: true, value: jsdomWindow._localStorage },
  sessionStorage: { configurable: true, value: jsdomWindow._sessionStorage },
});

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  sessionStorage.clear();
});
afterAll(() => server.close());
