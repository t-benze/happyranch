import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
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
    expect(main.indexOf('bootstrapDocumentLocale()')).toBeLessThan(main.indexOf('createRoot'));
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
    return waitFor(() =>
      screen.getByRole('heading', { name: /Connect your agentic CLI/i }),
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
    expect(main).toContain('const initialLocale = bootstrapDocumentLocale()');
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
});
