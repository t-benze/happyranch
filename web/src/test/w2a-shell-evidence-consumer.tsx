/**
 * TEST-ONLY W2a shell browser-evidence consumer (THR-118).
 *
 * Never imported by a shipping entry point. The evidence-only Vite transform in
 * `vite.config.ts` injects it adjacent to `<AppRoutes />` inside the real
 * `main.tsx -> App -> createBrowserRouter -> AppShell -> I18nProvider`
 * composition, and injects `<ShellErrorTrigger />` inside the real
 * `AppShellErrorBoundary` wrapper, and only when `I18N_W2A_EVIDENCE` is set.
 * Every ordinary build, Storybook, Vitest and CI run leaves that variable
 * unset, so this module is absent.
 *
 * W2a review R3: the record is now the **actual first committed Sidebar/AppBar
 * output read from the real DOM**, captured in a layout effect the first time
 * the shell is connected, and together with `<html lang>` at that same instant.
 * It is frozen on first capture and never overwritten by a later locale
 * correction, so a shell that renders the wrong language first and repairs
 * itself later cannot pass the positive acceptance predicate.
 *
 * It also provides the test-only controls the supported
 * `web/scripts/w2a-shell-browser-evidence.mjs` harness drives. These are not a
 * shipping language selector.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useI18n } from '@/hooks/i18n';
import { CommandPalette } from '@/design-system/patterns/CommandPalette';

declare global {
  interface Window {
    __hrFirstShell?: {
      /** Actual committed Sidebar anchor labels (nav + footer Settings). */
      navLabels: string[];
      /** Actual committed AppBar page title span. */
      title: string | null;
      locale: string;
      htmlLang: string | null;
      /** Real navigator read-back at the capture instant. */
      navigatorLanguage: string | null;
      navigatorLanguages: string[];
      /** Provenance marker: this record came from committed DOM, not a probe. */
      source: 'committed-dom';
      capturedAt: number;
    };
  }
}

interface CommittedShell {
  navLabels: string[];
  title: string | null;
  htmlLang: string | null;
}

/**
 * Read the actually rendered Sidebar/AppBar output. Returns `null` until the
 * shell has committed; never computes expectations from the catalog.
 */
export function readCommittedShell(root: ParentNode = document): CommittedShell | null {
  const sidebar = root.querySelector('aside[role="navigation"]');
  const nav = sidebar ? sidebar.querySelector('nav') : null;
  if (!sidebar || !nav) return null;
  const navLabels = [...sidebar.querySelectorAll('a')].map((a) => (a.textContent || '').trim());
  const appBarSpan = root.querySelector('main')?.previousElementSibling?.querySelector('span');
  return {
    navLabels,
    title: appBarSpan ? (appBarSpan.textContent || '').trim() : null,
    htmlLang: root.ownerDocument
      ? root.ownerDocument.documentElement.getAttribute('lang')
      : document.documentElement.getAttribute('lang'),
  };
}

function readNavigator(): { navigatorLanguage: string | null; navigatorLanguages: string[] } {
  try {
    return {
      navigatorLanguage: typeof navigator !== 'undefined' ? navigator.language : null,
      navigatorLanguages:
        typeof navigator !== 'undefined' && Array.isArray(navigator.languages)
          ? [...navigator.languages]
          : [],
    };
  } catch {
    return { navigatorLanguage: null, navigatorLanguages: [] };
  }
}

/** First-commit shell record + test-only controls. */
export function ShellEvidenceConsumer(): JSX.Element {
  const { locale, setLocale } = useI18n();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Always read the provider's live locale at capture time without re-running
  // the observation effect (which must never overwrite the frozen record).
  const localeRef = useRef(locale);
  localeRef.current = locale;

  useLayoutEffect(() => {
    if (typeof window === 'undefined' || window.__hrFirstShell) return;
    const capture = (): boolean => {
      if (window.__hrFirstShell) return true;
      const committed = readCommittedShell();
      // Wait for the real shell to connect; once captured it is frozen.
      if (!committed || committed.navLabels.length === 0) return false;
      window.__hrFirstShell = {
        ...committed,
        locale: localeRef.current,
        ...readNavigator(),
        source: 'committed-dom',
        capturedAt:
          typeof performance !== 'undefined' && typeof performance.now === 'function'
            ? performance.now()
            : Date.now(),
      };
      return true;
    };
    if (capture()) return;
    const observer = new MutationObserver(() => {
      if (capture()) observer.disconnect();
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['lang'],
    });
    return () => observer.disconnect();
  }, []);

  return (
    <div
      data-testid="w2a-evidence-controls"
      className="fixed right-1 bottom-1 z-[9999] flex gap-1"
    >
      <button data-testid="w2a-set-en" type="button" onClick={() => setLocale('en')}>
        en
      </button>
      <button data-testid="w2a-set-zh" type="button" onClick={() => setLocale('zh-CN')}>
        zh
      </button>
      <PaletteProbe open={paletteOpen} onOpen={() => setPaletteOpen(true)} />
    </div>
  );
}

/** Test-only real-boundary error trigger (injected inside AppShellErrorBoundary). */
export function ShellErrorTrigger(): JSX.Element {
  const [boom, setBoom] = useState(false);
  if (boom) throw new Error('W2A evidence error detail');
  return (
    <button
      data-testid="w2a-error-trigger"
      type="button"
      className="fixed bottom-1 left-1 z-[9999]"
      onClick={() => setBoom(true)}
    >
      error
    </button>
  );
}

/**
 * Negative-control-only correction. Injected only in the
 * `I18N_W2A_EVIDENCE=negative` build, alongside the mismatched provider
 * resolution that transform also installs in `main.tsx`. A passive effect
 * corrects the locale after the first commit, so the real shell renders the
 * wrong language first and repairs itself afterwards — proving the harness's
 * frozen first-commit predicate is causal (it must reject the first shell)
 * rather than an eventual snapshot.
 */
export function ShellEvidenceCorrection(): null {
  const { setLocale } = useI18n();
  useEffect(() => {
    setLocale('zh-CN');
  }, [setLocale]);
  return null;
}

function PaletteProbe({ open, onOpen }: { open: boolean; onOpen: () => void }): JSX.Element {
  const { t } = useI18n();
  return (
    <>
      <button data-testid="w2a-open-palette" type="button" onClick={onOpen}>
        palette
      </button>
      <CommandPalette
        open={open}
        onClose={() => undefined}
        onSelect={() => undefined}
        sections={[
          {
            label: t('palette.section.tasks'),
            items: [
              { key: 'task:1', primary: 'TASK-1 · 刷新酒店列表', href: '/tasks/1' },
              { key: 'task:2', primary: 'TASK-2 · 更新签证规则', href: '/tasks/2' },
            ],
          },
        ]}
        title={t('palette.title')}
        description={t('palette.description')}
        searchPlaceholder={t('palette.searchPlaceholder')}
        searchLabel={t('palette.searchLabel')}
        resultsLabel={t('palette.resultsLabel')}
        noMatches={t('palette.noMatches')}
        nothingLoaded={t('palette.nothingLoaded')}
        navigateLabel={t('palette.footer.navigate')}
        openLabel={t('palette.footer.open')}
        closeLabel={t('palette.footer.close')}
        closeAriaLabel={t('common.close')}
      />
    </>
  );
}
