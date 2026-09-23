import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from './src/test/server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  if (typeof sessionStorage !== 'undefined') sessionStorage.clear();
  // Locale/theme/density preferences are persisted per test; reset them so a
  // saved locale from one test never leaks into the next. Suites that opt into
  // the `node` environment have no Web Storage or document, so guard both.
  if (typeof localStorage !== 'undefined') localStorage.clear();
  if (typeof document !== 'undefined') document.documentElement.setAttribute('lang', 'en');
});
afterAll(() => server.close());
