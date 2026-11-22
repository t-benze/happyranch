import type { ReactNode } from 'react';
import { Moon, Sun } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useI18n, useTranslation } from '@/hooks/i18n';
import { useNativeRecovery } from '@/hooks/nativeRecovery';
import { useTheme } from '@/hooks/theme';
import { translate, type Locale, type MessageKey } from '@/lib/i18n';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/design-system/primitives/Select';
import { features } from '@/config/features';
import { useTopbarSlotRefs } from './TopbarSlots';

/**
 * AppBar — persistent top app bar (THR-030 BUG-04/05/06).
 *
 * Renders to the right of the Sidebar, above the routed content: current page
 * name on the left; language selector and theme toggle on the right.
 *
 * The page name is derived purely from the URL pathname (no data fetch), so it
 * stays correct on every surface without new client state. THR-118 W2a keeps
 * `pageTitleFromPath` a pure helper but gives it an explicit locale argument;
 * the React hook lives in the `AppBar` consumer, never in the helper.
 *
 * THR-376 PR2: every page gets the Pasture `.topbar` (ds.css): 56px tall,
 * 26px inline padding, 14px gap, `--bg` at 80% (`bg-surface-canvas/80` compiles to the
 * same `color-mix(in oklab, var(--color-surface-canvas) 80%, transparent)`), and the
 * `.page-title` in Newsreader 18/500 with -0.2px tracking (THR-376 PR12).
 * There is no per-page presentation.
 * Optional `status` renders after the title and `actions` before the
 * language/theme controls; both are omitted when not supplied.
 *
 * THR-376 r2 PR-S1 (A1b/A2): the routed page fills the same two places through
 * `TopbarStatus` / `TopbarActions` (./TopbarSlots), which portal into the slot
 * elements below. Empty slots are `empty:hidden` so they add no flex gap.
 *
 * R1 (narrow screens): from `lg` up the status and actions sit inline in the
 * one 56px row (the slot group fills the space between title and controls).
 * Below `lg` the bar wraps: row 1 keeps the title and the language/theme
 * controls at 56px, and the slot group moves to a full-width second row
 * (status left, actions right) so neither is squeezed or pushed off-screen.
 * The group is hidden there while every slot is empty.
 */

const SECTION_TITLES: Record<string, MessageKey> = {
  dashboard: 'shell.title.home',
  threads: 'shell.title.threads',
  tasks: 'shell.title.tasks',
  agents: 'shell.title.agents',
  skills: 'shell.title.skills',
  kb: 'shell.title.knowledge',
  usage: 'shell.title.usage',
  dreams: 'shell.title.dreams',
  // THR-305: no Work Hours title while the UI is off; the not-found page then
  // keeps the ordinary fallback title.
  ...(features.work_hours_ui ? { 'work-hours': 'shell.title.workHours' as MessageKey } : {}),
  audit: 'shell.title.audit',
  settings: 'shell.title.settings',
  jobs: 'shell.title.jobs',
  todos: 'shell.title.todos',
  workflows: 'shell.title.workflows',
  health: 'shell.title.runtimeHealth',
};

/** Pure pathname -> localized page title helper (explicit locale). */
export function pageTitleFromPath(pathname: string, locale: Locale): string {
  // Onboarding is a global (non-org-scoped) surface, so it isn't in the
  // /orgs/:slug/:section map above.
  if (pathname.startsWith('/onboarding')) return translate(locale, 'shell.title.getStarted');
  const match = pathname.match(/^\/orgs\/[^/]+\/([^/]+)/);
  const section = match?.[1];
  const key = section ? SECTION_TITLES[section] : undefined;
  return translate(locale, key ?? 'shell.title.home');
}

export interface AppBarProps {
  /** Page status chip(s), rendered right after the page title. */
  status?: ReactNode;
  /** Page-level actions, rendered at the right before the shell controls. */
  actions?: ReactNode;
}

export function AppBar({ status, actions }: AppBarProps): JSX.Element {
  const location = useLocation();
  const { locale, setLocale, t } = useI18n();
  // THR-387 S1b: the org-less recovery Settings (/settings/*) exists only
  // during a native recovery state; outside it the URL keeps today's title.
  const recovery = useNativeRecovery();
  const title =
    recovery && /^\/settings(\/|$)/.test(location.pathname)
      ? t('shell.title.settings')
      : pageTitleFromPath(location.pathname, locale);
  const { statusRef, actionsRef } = useTopbarSlotRefs();

  return (
    <div className="border-border-default bg-surface-canvas/80 flex h-14 shrink-0 items-center gap-3.5 border-b px-6.5 max-lg:h-auto max-lg:min-h-14 max-lg:flex-wrap max-lg:gap-y-2 max-lg:py-3">
      <span className="text-text-primary font-display text-lg font-medium tracking-[-0.2px]">{title}</span>
      <div className="flex min-w-0 items-center gap-x-3.5 gap-y-2 max-lg:order-last max-lg:hidden max-lg:w-full max-lg:flex-wrap max-lg:has-[>:not(:empty)]:flex lg:flex-1">
        {status != null && <div className="flex min-w-0 items-center gap-2">{status}</div>}
        <div ref={statusRef} data-topbar-slot="status" className="flex min-w-0 flex-wrap items-center gap-2 empty:hidden" />
        {actions != null && <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div>}
        <div ref={actionsRef} data-topbar-slot="actions" className="ml-auto flex shrink-0 items-center gap-2 empty:hidden" />
      </div>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <Select value={locale} onValueChange={(next) => {
          if (next === 'en' || next === 'zh-CN') setLocale(next);
        }}>
          <SelectTrigger aria-label={t('common.language')} title={t('common.language')} className="h-button-icon-size">
            <SelectValue>
              <span lang={locale}>{t(locale === 'en' ? 'shell.languageEnglish' : 'shell.languageChinese')}</span>
            </SelectValue>
          </SelectTrigger>
          <SelectContent align="end">
            <SelectItem value="en"><span lang="en">{t('shell.languageEnglish')}</span></SelectItem>
            <SelectItem value="zh-CN"><span lang="zh-CN">{t('shell.languageChinese')}</span></SelectItem>
          </SelectContent>
        </Select>
        <ThemeToggle />
      </div>
    </div>
  );
}

function ThemeToggle(): JSX.Element {
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation();
  const isDark = theme === 'dark';
  const label = isDark ? t('shell.switchToLight') : t('shell.switchToDark');
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
      className="text-text-secondary hover:bg-surface-raised hover:text-text-primary focus-visible:ring-border-default inline-flex h-button-icon-size w-button-icon-size items-center justify-center rounded-sm transition-colors focus-visible:ring-1 focus-visible:outline-none"
    >
      {isDark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}
