/**
 * TEST-ONLY W2a shell browser-evidence consumer (THR-118).
 *
 * Never imported by a shipping entry point. The evidence-only Vite transform in
 * `vite.config.ts` injects it adjacent to `<AppRoutes />` inside the real
 * `main.tsx -> App -> AppShell -> I18nProvider` composition, and injects
 * `<ShellErrorTrigger />` inside the real `AppShellErrorBoundary` wrapper, and
 * only when `I18N_BROWSER_EVIDENCE` is set. Every ordinary build, Storybook,
 * Vitest and CI run leaves that variable unset, so this module is absent.
 *
 * It provides:
 *   - a first-render record of the shell copy (`html.lang` + the exact AppBar
 *     title and Sidebar nav labels the first commit will use), captured during
 *     render before any effect can correct it; and
 *   - test-only controls (locale switch, error trigger, palette probe) the
 *     supported `web/scripts/w2a-shell-browser-evidence.mjs` harness drives.
 *     These are not a shipping language selector.
 */
import { useState } from 'react';
import { useI18n } from '@/hooks/i18n';
import { pageTitleFromPath } from '@/design-system/layouts/AppShell/AppBar';
import { CommandPalette } from '@/design-system/patterns/CommandPalette';
import type { MessageKey } from '@/lib/i18n';

const NAV_KEYS: readonly MessageKey[] = [
  'shell.nav.home',
  'shell.nav.threads',
  'shell.nav.tasks',
  'shell.nav.jobs',
  'shell.nav.todos',
  'shell.nav.agents',
  'shell.nav.workHours',
  'shell.nav.skills',
  'shell.nav.knowledge',
  'shell.nav.artifacts',
  'shell.nav.audit',
  'shell.nav.dreams',
  'shell.nav.usage',
  'shell.nav.health',
  'shell.nav.settings',
];

declare global {
  interface Window {
    __hrFirstShell?: {
      navLabels: string[];
      title: string;
      locale: string;
      htmlLang: string | null;
      capturedAt: number;
    };
  }
}

/** First-commit shell record + test-only controls. */
export function ShellEvidenceConsumer(): JSX.Element {
  const { locale, t, setLocale } = useI18n();
  const [paletteOpen, setPaletteOpen] = useState(false);

  if (typeof window !== 'undefined' && !window.__hrFirstShell) {
    window.__hrFirstShell = {
      navLabels: NAV_KEYS.map((key) => t(key)),
      title: pageTitleFromPath(window.location.pathname, locale),
      locale,
      htmlLang: document.documentElement.getAttribute('lang'),
      capturedAt:
        typeof performance !== 'undefined' && typeof performance.now === 'function'
          ? performance.now()
          : Date.now(),
    };
  }

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
      />
    </>
  );
}
