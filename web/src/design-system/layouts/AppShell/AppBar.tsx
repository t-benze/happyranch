import { Bot, Moon, Sun } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useI18n, useTranslation } from '@/hooks/i18n';
import { useTheme } from '@/hooks/theme';
import { translate, type Locale, type MessageKey } from '@/lib/i18n';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/design-system/primitives/Select';

/**
 * AppBar — persistent top app bar (THR-030 BUG-04/05/06).
 *
 * Renders to the right of the Sidebar, above the routed content: current page
 * name on the left; assistant avatar, language selector and theme toggle on the right. The avatar
 * opens the global Assistant Dock via the app-wide `[data-assistant-open]`
 * click handler (AssistantDockHost) — same wiring the search pill used before
 * it was replaced by the avatar entry point (THR-056 PR-6).
 *
 * The page name is derived purely from the URL pathname (no data fetch), so it
 * stays correct on every surface without new client state. THR-118 W2a keeps
 * `pageTitleFromPath` a pure helper but gives it an explicit locale argument;
 * the React hook lives in the `AppBar` consumer, never in the helper.
 */

const SECTION_TITLES: Record<string, MessageKey> = {
  dashboard: 'shell.title.home',
  threads: 'shell.title.threads',
  tasks: 'shell.title.tasks',
  agents: 'shell.title.agents',
  skills: 'shell.title.skills',
  kb: 'shell.title.knowledge',
  artifacts: 'shell.title.artifacts',
  usage: 'shell.title.usage',
  dreams: 'shell.title.dreams',
  'work-hours': 'shell.title.workHours',
  audit: 'shell.title.audit',
  settings: 'shell.title.settings',
  jobs: 'shell.title.jobs',
  health: 'shell.title.runtimeHealth',
  assistant: 'shell.title.assistant',
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
  /** Prototypes have no AssistantDockHost, so they omit its unavailable control. */
  showAssistantControl?: boolean;
  presentation?: 'tasks';
}

export function AppBar({ showAssistantControl = true, presentation }: AppBarProps): JSX.Element {
  const location = useLocation();
  const { locale, setLocale, t } = useI18n();
  const title = pageTitleFromPath(location.pathname, locale);

  return (
    <div className={`border-border bg-bg-subtle flex shrink-0 items-center border-b ${presentation === 'tasks' ? 'tasks-appbar' : 'h-12 gap-4 px-5'}`}>
      <span className={`text-fg font-medium ${presentation === 'tasks' ? 'font-display text-lg' : 'text-sm'}`}>{title}</span>
      <div className="ml-auto flex shrink-0 items-center gap-2">
        {showAssistantControl && (
          <button
            type="button"
            data-assistant-open="true"
            aria-label={t('shell.openAssistant')}
            title={t('shell.openAssistant')}
            className="bg-accent text-accent-fg hover:bg-accent-hover focus-visible:ring-accent inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <Bot size={16} aria-hidden="true" />
          </button>
        )}
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
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-border inline-flex h-button-icon-size w-button-icon-size items-center justify-center rounded transition-colors focus-visible:ring-1 focus-visible:outline-none"
    >
      {isDark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}
