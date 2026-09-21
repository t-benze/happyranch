/**
 * TEST-ONLY first-commit evidence consumer (THR-118 W1).
 *
 * This module is never imported by a shipping entry point. It is injected
 * adjacent to `<AppRoutes />` inside the real
 * `main.tsx -> App -> createBrowserRouter -> AppShell -> I18nProvider`
 * composition by the evidence-only Vite transform in `vite.config.ts`, and only
 * when `I18N_BROWSER_EVIDENCE` is set. Every ordinary `npm run build`,
 * `npm run build-storybook`, Vitest and CI run leaves that variable unset, so
 * the transform returns `null` and this file is absent from the bundle.
 *
 * It records the consumer text the component is about to commit on its FIRST
 * render, together with `<html lang>` at that same instant. Capturing during
 * render is deliberately earlier than any `useLayoutEffect`/`useEffect`, so a
 * later effect that corrects a mismatched locale can never rewrite the
 * observation. The browser harness asserts on this frozen value, not on an
 * eventual DOM snapshot.
 */
import { useEffect } from 'react';
import { useI18n } from '@/hooks/i18n';

declare global {
  interface Window {
    __hrFirstConsumer?: {
      seq: number;
      /** The catalog string this component is about to commit. */
      text: string;
      locale: string;
      /** `<html lang>` at the exact first-render instant. */
      htmlLang: string | null;
      capturedAt: number;
    };
  }
}

/**
 * The minimal bilingual consumer. It renders one real catalog key
 * (`common.translatedProbe`: English "Translated probe" / zh-CN "已翻译探针") so
 * the harness observes an actual translation from the real catalogs, never an
 * independently calculated expected string or a new product key.
 */
export function I18nEvidenceConsumer(): JSX.Element {
  const { locale, t } = useI18n();
  const text = t('common.translatedProbe');

  // First render == first DOM commit. Capture once, before any effect can run.
  if (typeof window !== 'undefined' && !window.__hrFirstConsumer) {
    window.__hrFirstConsumer = {
      seq: 1,
      text,
      locale,
      htmlLang: document.documentElement.getAttribute('lang'),
      capturedAt:
        typeof performance !== 'undefined' && typeof performance.now === 'function'
          ? performance.now()
          : Date.now(),
    };
  }

  return (
    <div data-testid="evidence-first-consumer" data-locale={locale} data-text={text}>
      {text}
    </div>
  );
}

/**
 * Negative-control-only correction. It is injected only in the
 * `I18N_BROWSER_EVIDENCE=negative` build, alongside the mismatched provider
 * resolution that transform also installs in `main.tsx`. A passive effect
 * corrects the locale after the first commit, proving the harness's first-commit
 * assertion is causal (it must fail) rather than an eventual snapshot.
 */
export function I18nEvidenceCorrection(): null {
  const { setLocale } = useI18n();
  useEffect(() => {
    setLocale('zh-CN');
  }, [setLocale]);
  return null;
}

/**
 * Negative-control-only document-locale pin. The real `I18nProvider` applies
 * its (here deliberately mismatched) resolution to `<html lang>` in the same
 * render pass, so to reproduce the requirement exactly — "mismatched initial
 * provider locale while the document locale remains correct" — this sibling is
 * rendered BEFORE the consumer and pins `<html lang>` back to `zh-CN` during
 * render. Only the negative build injects it; the positive/shipping paths never
 * do.
 */
export function I18nEvidenceDocumentLocale(): null {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('lang', 'zh-CN');
  }
  return null;
}
