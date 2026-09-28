/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function resolveDaemonPort(): number {
  const home = process.env.HAPPYRANCH_DAEMON_HOME || path.join(os.homedir(), '.happyranch');
  const portFile = path.join(home, 'daemon.port');
  try {
    const raw = fs.readFileSync(portFile, 'utf-8').trim();
    const port = parseInt(raw, 10);
    if (!Number.isNaN(port) && port > 0) return port;
  } catch {
    /* fall through to default */
  }
  return 8765;
}

const EVIDENCE_ENV = process.env.I18N_BROWSER_EVIDENCE ?? '';
const EVIDENCE_NEGATIVE = EVIDENCE_ENV === 'negative';
const EVIDENCE_ENABLED = EVIDENCE_ENV === '1' || EVIDENCE_NEGATIVE;
const EVIDENCE_APP_IMPORT = "import { AppRoutes } from './routes';";
const EVIDENCE_APP_ANCHOR = '        <AppRoutes />';
const EVIDENCE_MAIN_MISMATCH_ANCHOR = 'const initialLocale = bootstrapDocumentLocale();';
const EVIDENCE_MAIN_PROVIDER_ANCHOR = '<App initialLocale={initialLocale} />';

/**
 * Evidence-only W2a shell instrumentation (THR-118). Independent of the W1
 * `I18N_BROWSER_EVIDENCE` flag, so W1 evidence builds stay byte-identical. It
 * is a NO-OP unless `I18N_W2A_EVIDENCE` is `1` or `negative`.
 *
 * `I18N_W2A_EVIDENCE=negative` additionally installs the passive correction
 * component and hands `main.tsx` a deliberately mismatched provider resolution
 * (while `bootstrapDocumentLocale()` still computes the document locale from
 * the saved preference). That makes the real shell commit the wrong language
 * first and repair itself afterwards, so the harness's frozen first-commit
 * predicate is proven causal.
 */
const W2A_EVIDENCE =
  process.env.I18N_W2A_EVIDENCE === '1' || process.env.I18N_W2A_EVIDENCE === 'negative';
const W2A_NEGATIVE = process.env.I18N_W2A_EVIDENCE === 'negative';
const W2A_ROUTES_OUTLET_ANCHOR = '            <Outlet />';

/**
 * Evidence-only build instrumentation for the THR-118 W1 browser harness.
 *
 * It is a NO-OP unless `I18N_BROWSER_EVIDENCE` is set. The default (`unset`),
 * every ordinary `npm run build`, `build-storybook`, Vitest run and CI build
 * therefore produce the shipping bytes untouched.
 *
 * When enabled it injects the test-only `I18nEvidenceConsumer` adjacent to
 * `<AppRoutes />` inside the REAL
 * `main.tsx -> App -> createBrowserRouter -> AppShell -> I18nProvider`
 * composition, so the harness can observe the first committed translated
 * consumer text through the production provider/router rather than a second
 * provider or a MemoryRouter. It never rewrites the resolver, the bootstrap
 * snapshot handoff, the provider or the router.
 *
 * `I18N_BROWSER_EVIDENCE=negative` (the isolated negative control only) also
 * hands the provider a deliberately mismatched `{locale:'en', source:'saved'}`
 * resolution in `main.tsx` — while the document locale from
 * `bootstrapDocumentLocale()` stays correct — and injects the passive-effect
 * correction component. That produces an English first commit under a correct
 * `zh-CN` `<html lang>` which a later effect repairs; it exists only to prove
 * the harness's first-commit assertion is causal.
 */
function i18nEvidenceInstrumentation() {
  return {
    name: 'i18n-browser-evidence-instrumentation',
    enforce: 'pre' as const,
    transform(code: string, id: string) {
      if (!EVIDENCE_ENABLED && !W2A_EVIDENCE) return null;
      const clean = id.split('?')[0].replace(/\\/g, '/');

      if (W2A_EVIDENCE && clean.endsWith('/src/App.tsx')) {
        if (!code.includes(EVIDENCE_APP_IMPORT) || !code.includes(EVIDENCE_APP_ANCHOR)) {
          throw new Error('w2a evidence: App.tsx instrumentation anchor not found');
        }
        const consumerImport = W2A_NEGATIVE
          ? "import { ShellEvidenceConsumer, ShellEvidenceCorrection } from './test/w2a-shell-evidence-consumer';"
          : "import { ShellEvidenceConsumer } from './test/w2a-shell-evidence-consumer';";
        const injected = W2A_NEGATIVE
          ? `${EVIDENCE_APP_ANCHOR}\n        <ShellEvidenceConsumer />\n        <ShellEvidenceCorrection />`
          : `${EVIDENCE_APP_ANCHOR}\n        <ShellEvidenceConsumer />`;
        return {
          code: code
            .replace(EVIDENCE_APP_IMPORT, `${EVIDENCE_APP_IMPORT}\n${consumerImport}`)
            .replace(EVIDENCE_APP_ANCHOR, injected),
          map: null,
        };
      }

      if (W2A_EVIDENCE && clean.endsWith('/src/routes.tsx')) {
        if (!code.includes(W2A_ROUTES_OUTLET_ANCHOR)) {
          throw new Error('w2a evidence: routes.tsx Outlet anchor not found');
        }
        return {
          code: code
            .replace(
              "import { AppBar } from '@/design-system/layouts/AppShell/AppBar';",
              "import { AppBar } from '@/design-system/layouts/AppShell/AppBar';\nimport { ShellErrorTrigger } from './test/w2a-shell-evidence-consumer';",
            )
            .replace(
              W2A_ROUTES_OUTLET_ANCHOR,
              `${W2A_ROUTES_OUTLET_ANCHOR}\n            <ShellErrorTrigger />`,
            ),
          map: null,
        };
      }

      // Negative control (W1 or W2a): hand the real provider a mismatched
      // resolution while `bootstrapDocumentLocale()` keeps the document locale
      // from the saved preference. Handled before the W1 gate because the W2a
      // negative is driven by `I18N_W2A_EVIDENCE=negative` alone.
      if ((EVIDENCE_NEGATIVE || W2A_NEGATIVE) && clean.endsWith('/src/main.tsx')) {
        if (
          !code.includes(EVIDENCE_MAIN_MISMATCH_ANCHOR) ||
          !code.includes(EVIDENCE_MAIN_PROVIDER_ANCHOR)
        ) {
          throw new Error('i18n evidence: main.tsx negative-control anchor not found');
        }
        return {
          code: code
            .replace(
              EVIDENCE_MAIN_MISMATCH_ANCHOR,
              `${EVIDENCE_MAIN_MISMATCH_ANCHOR}\n// NEGATIVE CONTROL ONLY: mismatched provider locale; the document locale above stays correct.\nconst evidenceMismatchedResolution = { locale: 'en' as const, source: 'saved' as const };`,
            )
            .replace(EVIDENCE_MAIN_PROVIDER_ANCHOR, '<App initialLocale={evidenceMismatchedResolution} />'),
          map: null,
        };
      }

      if (!EVIDENCE_ENABLED) return null;

      if (clean.endsWith('/src/App.tsx')) {
        if (!code.includes(EVIDENCE_APP_IMPORT) || !code.includes(EVIDENCE_APP_ANCHOR)) {
          throw new Error('i18n evidence: App.tsx instrumentation anchor not found');
        }
        const consumerImport = EVIDENCE_NEGATIVE
          ? "import { I18nEvidenceConsumer, I18nEvidenceCorrection, I18nEvidenceDocumentLocale } from './test/i18n-evidence-consumer';"
          : "import { I18nEvidenceConsumer } from './test/i18n-evidence-consumer';";
        const injected = EVIDENCE_NEGATIVE
          ? `${EVIDENCE_APP_ANCHOR}\n        <I18nEvidenceDocumentLocale />\n        <I18nEvidenceConsumer />\n        <I18nEvidenceCorrection />`
          : `${EVIDENCE_APP_ANCHOR}\n        <I18nEvidenceConsumer />`;
        return {
          code: code
            .replace(EVIDENCE_APP_IMPORT, `${EVIDENCE_APP_IMPORT}\n${consumerImport}`)
            .replace(EVIDENCE_APP_ANCHOR, injected),
          map: null,
        };
      }

      return null;
    },
  };
}

export default defineConfig({
  plugins: [i18nEvidenceInstrumentation(), react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${resolveDaemonPort()}`,
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    css: false,
  },
});
