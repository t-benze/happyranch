import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from '@/App';
import { bootstrapDocumentLocale, LOCALE_STORAGE_KEY } from '@/lib/i18n';
import { server } from '@/test/server';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, '../..');

function read(relativePath: string): string {
  return readFileSync(join(webRoot, relativePath), 'utf8');
}

describe('production harness wiring (W1 acceptance case 7)', () => {
  it('App startup mounts the routes under the i18n provider', () => {
    const app = read('src/App.tsx');
    expect(app).toContain('I18nProvider');
    expect(app).toContain('@/hooks/i18n');
  });

  it('main.tsx resolves the locale before the first React text', () => {
    const main = read('src/main.tsx');
    expect(main).toContain('bootstrapDocumentLocale');
    expect(main.indexOf("bootstrapDocumentLocale({ mode: 'full' })")).toBeLessThan(main.indexOf('createRoot'));
  });

  it('the shared test harness renders with the i18n provider', () => {
    expect(read('src/test/render.tsx')).toContain('I18nProvider');
  });

  it('Storybook preview decorator wraps stories in the i18n provider', () => {
    const preview = read('.storybook/preview.tsx');
    expect(preview).toContain('I18nProvider');
    expect(preview).toContain('@/hooks/i18n');
  });

  it('bootstrapDocumentLocale drives html lang from a saved preference', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    const resolution = bootstrapDocumentLocale();
    expect(resolution.locale).toBe('zh-CN');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });
});

describe('production App composition and startup handoff (W1 acceptance cases 4/7)', () => {
  function stubOnboarding(): void {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })),
      http.get('/api/v1/health/prereqs', () => HttpResponse.json({ prereqs: [] })),
    );
  }

  function expectOnboarding(): Promise<HTMLElement> {
    // Onboarding is W2b-translated: the shell may render it in either locale.
    return waitFor(() =>
      screen.getByRole('heading', { name: /Connect your agentic CLI|连接你的智能体 CLI/ }),
    );
  }

  function renderShell(initialLocale?: { locale: 'en' | 'zh-CN'; source: 'saved' | 'native' }) {
    return render(
      <MemoryRouter initialEntries={['/']}>
        <AppShell initialLocale={initialLocale} />
      </MemoryRouter>,
    );
  }

  it('main.tsx hands its single resolution to App, which forwards it to the shell', () => {
    const main = read('src/main.tsx');
    expect(main).toContain("const initialLocale = bootstrapDocumentLocale({ mode: 'full' })");
    expect(main).toContain('<App initialLocale={initialLocale}');
    const app = read('src/App.tsx');
    expect(app).toContain('AppShell initialLocale={initialLocale}');
    expect(app).toContain('initialResolution={initialLocale}');
  });

  it('renders the production composition at a handed locale', async () => {
    stubOnboarding();
    renderShell({ locale: 'zh-CN', source: 'saved' });
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
    await expectOnboarding();
  });

  it('a direct shell render with no handed resolution resolves the initial locale itself', async () => {
    stubOnboarding();
    renderShell();
    expect(document.documentElement.getAttribute('lang')).toBe('en');
    await expectOnboarding();
  });

  it('keeps one resolution under StrictMode', async () => {
    stubOnboarding();
    localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
    const resolution = bootstrapDocumentLocale();
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/']}>
          <AppShell initialLocale={resolution} />
        </MemoryRouter>
      </StrictMode>,
    );
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
    await expectOnboarding();
  });
});

describe('browser-evidence instrumentation is test-only and gated (W1 acceptance case 8)', () => {
  it('the evidence Vite transform is disabled unless I18N_BROWSER_EVIDENCE is set', () => {
    const config = read('vite.config.ts');
    expect(config).toContain('process.env.I18N_BROWSER_EVIDENCE');
    expect(config).toContain("EVIDENCE_ENV === '1' || EVIDENCE_NEGATIVE");
    expect(config).toContain('if (!EVIDENCE_ENABLED) return null');
  });

  it('injects the consumer adjacent to AppRoutes inside the real provider/router composition', () => {
    const config = read('vite.config.ts');
    // The injection targets the real App.tsx sibling of AppRoutes, never a
    // replacement provider/router, and preserves the AppRoutes anchor.
    expect(config).toContain("clean.endsWith('/src/App.tsx')");
    expect(config).toContain('const EVIDENCE_APP_ANCHOR = ' + "'        <AppRoutes />'");
    expect(config).toContain('<I18nEvidenceConsumer />');
    // The negative control additionally swaps the handed resolution in main.tsx
    // and pins the document locale so only the provider locale is mismatched.
    expect(config).toContain('EVIDENCE_NEGATIVE');
    expect(config).toContain('evidenceMismatchedResolution');
    expect(config).toContain('<I18nEvidenceDocumentLocale />');
  });

  it('shipping App.tsx never statically imports the evidence consumer', () => {
    expect(read('src/App.tsx')).not.toContain('i18n-evidence-consumer');
    expect(read('src/main.tsx')).not.toContain('i18n-evidence-consumer');
  });

  it('the evidence consumer renders a real catalog key and captures the first render', () => {
    const consumer = read('src/test/i18n-evidence-consumer.tsx');
    expect(consumer).toContain("t('common.translatedProbe')");
    // Captured during render, before any layout/passive effect can correct it.
    expect(consumer).toContain('window.__hrFirstConsumer');
    expect(consumer).not.toMatch(/useLayoutEffect\(/);
  });

  it('the harness asserts the real navigator language input and the first commit', () => {
    const harness = read('scripts/i18n-browser-evidence.mjs');
    expect(harness).toContain("Object.defineProperty(Navigator.prototype, 'language'");
    expect(harness).toContain("Object.defineProperty(Navigator.prototype, 'languages'");
    expect(harness).toContain('window.__hrFirstConsumer');
    expect(harness).toContain('--negative');
    expect(harness).toContain('NEGATIVE CONTROL');
  });

  it('gates the W2a evidence transform and its causal negative control (W2a R3)', () => {
    const config = read('vite.config.ts');
    expect(config).toContain('process.env.I18N_W2A_EVIDENCE');
    expect(config).toContain("process.env.I18N_W2A_EVIDENCE === 'negative'");
    expect(config).toContain('ShellEvidenceConsumer');
    expect(config).toContain('ShellEvidenceCorrection');
    // The W2a negative installs the mismatched provider resolution in main.tsx
    // even when the W1 flag is unset.
    expect(config).toContain('EVIDENCE_NEGATIVE || W2A_NEGATIVE');
  });

  it('shipping sources never statically import the W2a evidence consumer (W2a R3)', () => {
    expect(read('src/App.tsx')).not.toContain('w2a-shell-evidence-consumer');
    expect(read('src/routes.tsx')).not.toContain('w2a-shell-evidence-consumer');
  });

  it('the W2a harness asserts the frozen committed-shell record and its negative control (W2a R3)', () => {
    const harness = read('scripts/w2a-shell-browser-evidence.mjs');
    expect(harness).toContain('window.__hrFirstShell');
    expect(harness).toContain('committed-dom');
    expect(harness).toContain("process.argv.includes('--negative')");
    expect(harness).toContain('NEGATIVE CONTROL');
    // Real focus/identity observations required by R4.
    expect(harness).toContain('document.activeElement');
    expect(harness).toContain('hrIdentity');
  });
});

describe('W5 production AppShell storage stage', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.lang = 'en';
  });

  function mountChineseShell() {
    sessionStorage.setItem('happyranch.token', 'tok');
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('zh-CN');
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['zh-CN', 'zh']);
    server.use(
      http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })),
      http.get('/api/v1/health/prereqs', () => HttpResponse.json({ prereqs: [] })),
    );
    return render(<MemoryRouter initialEntries={['/']}><AppShell initialLocale={{ locale: 'en', source: 'saved' }} /></MemoryRouter>);
  }

  it.each([
    { event: 'delete', key: LOCALE_STORAGE_KEY, value: null },
    { event: 'clear', key: null, value: null },
    { event: 'invalid', key: LOCALE_STORAGE_KEY, value: 'invalid' },
  ])('production AppShell uses full fallback after $event without storage echo', async ({ key, value }) => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
    mountChineseShell();
    await screen.findByRole('heading', { name: /Connect your agentic CLI/ });
    const write = vi.spyOn(Storage.prototype, 'setItem');
    act(() => {
      fireEvent(window, new StorageEvent('storage', { key, newValue: value, storageArea: localStorage }));
    });
    expect(document.documentElement.lang).toBe('zh-CN');
    expect(await screen.findByRole('heading', { name: /连接你的智能体 CLI/ })).toBeInTheDocument();
    expect(write).not.toHaveBeenCalledWith(LOCALE_STORAGE_KEY, expect.anything());
  });

  it('production AppShell falls back to Chinese when locale storage is unavailable', async () => {
    vi.spyOn(navigator, 'language', 'get').mockReturnValue('zh-CN');
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(['zh-CN']);
    sessionStorage.setItem('happyranch.token', 'tok');
    const original = Storage.prototype.getItem;
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(function (this: Storage, key) {
      if (key === LOCALE_STORAGE_KEY) throw new Error('unavailable');
      return original.call(this, key);
    });
    server.use(
      http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [] })),
      http.get('/api/v1/health/prereqs', () => HttpResponse.json({ prereqs: [] })),
    );
    render(<MemoryRouter initialEntries={['/']}><AppShell /></MemoryRouter>);
    expect(document.documentElement.lang).toBe('zh-CN');
    expect(await screen.findByRole('heading', { name: /连接你的智能体 CLI/ })).toBeInTheDocument();
  });
});

// This route-leaf scaffold observes the real main -> App -> AppShell -> provider
// handoff during render. Shipping connected DOM is owned by the ordinary browser
// cases; replacing page data here keeps network timing out of the startup oracle.
describe('W5 ordinary production startup', () => {
  afterEach(() => {
    vi.doUnmock('@/routes');
    vi.restoreAllMocks();
    document.getElementById('root')?.remove();
    document.documentElement.lang = 'en';
  });

  it.each([
    { label: 'missing Chinese', saved: null, languages: ['zh-CN', 'zh'], expected: 'zh-CN', text: '加载中…' },
    { label: 'invalid Chinese', saved: 'invalid', languages: ['zh-TW'], expected: 'zh-CN', text: '加载中…' },
    { label: 'saved English / Chinese', saved: 'en', languages: ['zh-CN'], expected: 'en', text: 'Loading…' },
    { label: 'saved Chinese / English', saved: 'zh-CN', languages: ['en-US'], expected: 'zh-CN', text: '加载中…' },
    { label: 'English', saved: null, languages: ['en-US'], expected: 'en', text: 'Loading…' },
    { label: 'unsupported', saved: null, languages: ['fr-FR'], expected: 'en', text: 'Loading…' },
    { label: 'ordered Chinese', saved: null, languages: ['fr-FR', 'zh-HK', 'en'], expected: 'zh-CN', text: '加载中…' },
    { label: 'ordered English', saved: null, languages: ['en-GB', 'zh-CN'], expected: 'en', text: 'Loading…' },
  ])('ordinary main: $label resolves before its first consumer and reads one snapshot', async ({ saved, languages, expected, text }) => {
    localStorage.clear();
    if (saved !== null) localStorage.setItem(LOCALE_STORAGE_KEY, saved);
    vi.spyOn(navigator, 'language', 'get').mockReturnValue(languages[0]);
    vi.spyOn(navigator, 'languages', 'get').mockReturnValue(languages);
    vi.resetModules();
    const observations: Array<{ locale: string; lang: string; text: string }> = [];
    vi.doMock('@/routes', async () => {
      const { useI18n } = await import('@/hooks/i18n');
      return { AppRoutes: function FirstConsumer() {
        const { locale, t } = useI18n();
        const ownedText = t('shell.loading');
        observations.push({ locale, lang: document.documentElement.lang, text: ownedText });
        return <p>{ownedText}</p>;
      } };
    });
    const { browserLocalePreferenceAdapter } = await import('@/lib/i18n');
    const snapshot = vi.spyOn(browserLocalePreferenceAdapter, 'readSnapshot');
    const write = vi.spyOn(Storage.prototype, 'setItem');
    const rootElement = document.createElement('div');
    rootElement.id = 'root';
    document.body.append(rootElement);
    const dom = await import('react-dom/client');
    const createRoot = vi.spyOn(dom.default, 'createRoot');
    const react = await import('react');
    try {
      await react.act(async () => { await import('@/main'); });
      expect(observations.length).toBeGreaterThan(0);
      for (const observed of observations) expect(observed).toEqual({ locale: expected, lang: expected, text });
      expect(rootElement.textContent).toBe(text);
      expect(snapshot).toHaveBeenCalledTimes(1);
      expect(write).not.toHaveBeenCalledWith(LOCALE_STORAGE_KEY, expect.anything());
      expect(localStorage.getItem(LOCALE_STORAGE_KEY)).toBe(saved);
    } finally {
      const root = createRoot.mock.results[0]?.value as { unmount: () => void } | undefined;
      await react.act(async () => { root?.unmount(); });
    }
  });
});
