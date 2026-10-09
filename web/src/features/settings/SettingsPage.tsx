/**
 * SettingsPage — full page (not dialog) with sticky left sub-nav + field panel.
 *
 * Sub-nav: Capacity · Organization · Executors · Preferences.
 * (THR-061 seq79: the Usage sub-tab was removed — token usage now lives on
 * the standalone /usage page.)
 * Each sub-nav item routes to /orgs/:slug/settings/:section.
 * The Organization section has real saves via PUT /settings/org.
 *
 * Interaction acceptance (per PRD §4.11):
 * - iAC1: real bookmarkable route, sub-nav switches panels
 * - iAC2: per-field live-vs-restart badges match daemon behavior
 * - iAC3: agent-name inputs autocomplete from real roster
 * - iAC4: no field implies @mention routing (§A.2)
 *
 * THR-118 W2c: the shell copy is translated, and a client-only Preferences
 * (language) panel is mounted OUTSIDE the settings-API gate so it renders and
 * works while `useSettings` is loading, has failed or has no data. The other
 * panels keep the existing loading/error/data gate unchanged. THR-118 W3b-2
 * enabled Preferences in ordinary production builds; W5 now uses full-mode
 * browser-language fallback when a saved preference is absent or invalid.
 */
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useParams,
} from 'react-router-dom';
import {
  Home as HomeIcon,
  Terminal,
  Gauge,
  Languages,
  type LucideIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useSettings } from '@/hooks/settings';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { PageHeader } from '@/design-system/patterns/PageHeader';

const SECTIONS = [
  { key: 'daemon-capacity', labelKey: 'settings.nav.daemonCapacity', icon: Gauge },
  { key: 'organization', labelKey: 'settings.nav.organization', icon: HomeIcon },
  { key: 'executors', labelKey: 'settings.nav.executors', icon: Terminal },
] as const satisfies ReadonlyArray<{
  key: string;
  labelKey: MessageKey;
  icon: LucideIcon;
}>;

/** Client-only section; listed last (W3b-2: always mounted). */
const PREFERENCES_SECTION = {
  key: 'preferences',
  labelKey: 'settings.nav.preferences',
  icon: Languages,
} as const satisfies { key: string; labelKey: MessageKey; icon: LucideIcon };

export type SettingsSection =
  | (typeof SECTIONS)[number]['key']
  | (typeof PREFERENCES_SECTION)['key'];

/**
 * SettingsPage — the outer shell: left sub-nav + right panel outlet.
 *
 * Rendered by the router at /orgs/:slug/settings and
 * /orgs/:slug/settings/:section.
 */
export function SettingsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const settingsQuery = useSettings();
  const { t } = useTranslation();

  const gated = (panel: ReactNode): JSX.Element => (
    <>
      {settingsQuery.isLoading && <div className="text-text-secondary flex-1 p-6 text-sm">{t('settings.page.loading')}</div>}
      {settingsQuery.isError && (
        <div className="text-feedback-danger flex-1 p-6 text-sm">
          {t('settings.page.loadError')}
          {settingsQuery.error?.message && <> {settingsQuery.error.message}</>}
        </div>
      )}
      {settingsQuery.data && <SettingsContent>{panel}</SettingsContent>}
    </>
  );

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      <header className="border-border-default border-b p-4">
        <PageHeader
          title={<span className="font-display">{t('settings.page.title')}</span>}
          meta={t('settings.page.meta')}
        />
      </header>

      <Routes>
        {/* Default, legacy and unknown routes resolve before the API gate. */}
        <Route index element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
        <Route path="assistant" element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
        <Route path="system" element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
        <Route path="agents" element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
        {/* Client-only Preferences: outside the settings-API gate. */}
        <Route
          path="preferences"
          element={
            <SettingsContent>
              <PreferencesPanel />
            </SettingsContent>
          }
        />
        <Route path="daemon-capacity" element={gated(<DaemonCapacityPanel />)} />
        <Route path="organization" element={gated(settingsQuery.data && <OrganizationPanel org={settingsQuery.data.org} />)} />
        <Route path="executors" element={gated(<ExecutorsPanel />)} />
        <Route path="*" element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
      </Routes>
    </div>
  );
}

/** a-settings set-wrap: nav + content column capped at 1000, centered. */
function SettingsContent({ children }: { children: ReactNode }): JSX.Element {
  return (
    <div
      className="max-w-content-narrow mx-auto flex min-h-0 min-w-0 w-full flex-1 flex-col overflow-hidden sm:flex-row"
      data-testid="settings-content"
    >
      <SettingsSubNav />
      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}

/**
 * SettingsSubNav — wrapped links below 640px; left rail at 640px and wider.
 * Each link navigates to /orgs/:slug/settings/:section.
 * Each link carries a leading icon (per design ref `a-settings`).
 * Active link uses Pasture rounded-full pill style.
 */
function SettingsSubNav(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const { t } = useTranslation();
  const sections = [...SECTIONS, PREFERENCES_SECTION];

  return (
    <aside className="border-border-default bg-surface-sunken w-full min-w-0 shrink-0 overflow-y-auto border-b p-3 sm:w-50 sm:border-r sm:border-b-0">
      <h3 className="text-overline text-text-secondary mb-2 tracking-wider uppercase">
        {t('settings.nav.heading')}
      </h3>
      <ul className="flex flex-wrap gap-0.5 sm:block sm:space-y-0.5">
        {sections.map((s) => (
          <li key={s.key} className="min-w-0">
            <NavLink
              to={`/orgs/${slug}/settings/${s.key}`}
              className={({ isActive }) =>
                `flex w-full min-w-0 items-center gap-2 rounded-full px-3 py-1 text-left text-sm transition-colors ${
                  isActive
                    ? 'bg-accent-soft text-accent-text'
                    : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'
                }`
              }
            >
              <s.icon size={16} aria-hidden="true" className="shrink-0" />
              <span className="min-w-0 break-words">{t(s.labelKey)}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </aside>
  );
}

// ----------------------------------------------------------------
// Panel components — imported lazily to avoid circular deps.
// The lazy imports allow each panel to use its own hooks.
// ----------------------------------------------------------------

import { OrganizationSection } from './sections/OrganizationSection';
import { ExecutorsSection } from './sections/ExecutorsSection';
import { DaemonCapacitySection } from './sections/DaemonCapacitySection';
import { PreferencesSection } from './sections/PreferencesSection';
import type { OrgSettings } from '@/lib/api/types';

function DaemonCapacityPanel(): JSX.Element {
  // The capacity section owns its own heading and description, matching the
  // approved desktop target. Repeating them here produced two stacked headers.
  return <div className="max-w-3xl p-6"><DaemonCapacitySection /></div>;
}

function OrganizationPanel({ org }: { org: OrgSettings }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">{t('settings.panel.organization.title')}</h2>
      <p className="text-text-secondary mb-4 text-sm">
        {t('settings.panel.organization.description')}
      </p>
      <OrganizationSection org={org} />
    </div>
  );
}

function ExecutorsPanel(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.panel.executors.title')}</h2>
      <p className="text-text-secondary mb-6 text-sm">
        {t('settings.panel.executors.description')}
      </p>
      <ExecutorsSection />
    </div>
  );
}

function PreferencesPanel(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl min-w-0 p-4 break-words sm:p-6">
      <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.panel.preferences.title')}</h2>
      <p className="text-text-secondary mb-6 text-sm">
        {t('settings.panel.preferences.description')}
      </p>
      <PreferencesSection />
    </div>
  );
}
