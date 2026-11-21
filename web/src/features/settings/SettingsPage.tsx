/**
 * SettingsPage — full page (not dialog) with left sub-nav + field panel.
 *
 * Sub-nav: Capacity · Organization · Executors · Coding · Preferences.
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
 *
 * THR-320: the Founder coding section reads its own org-keyed coding view, so
 * it is mounted outside the general settings gate with its own states, and is
 * keyed by org so a draft or late reply never crosses into another org.
 */
import { useState } from 'react';
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
  GitPullRequest,
  type LucideIcon,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { useSettings } from '@/hooks/settings';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey } from '@/lib/i18n';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { useNativeRecovery } from '@/hooks/nativeRecovery';
import { NativeRecoveryFence } from '@/shared/connection/NativeRecoveryPanel';

const SECTIONS = [
  { key: 'daemon-capacity', labelKey: 'settings.nav.daemonCapacity', icon: Gauge },
  { key: 'organization', labelKey: 'settings.nav.organization', icon: HomeIcon },
  { key: 'executors', labelKey: 'settings.nav.executors', icon: Terminal },
] as const satisfies ReadonlyArray<{
  key: string;
  labelKey: MessageKey;
  icon: LucideIcon;
}>;

/** Founder coding section; owns its own read, outside the settings gate. */
const CODING_SECTION = {
  key: 'coding',
  labelKey: 'settings.nav.coding',
  icon: GitPullRequest,
} as const satisfies { key: string; labelKey: MessageKey; icon: LucideIcon };

/** Client-only section; listed last (W3b-2: always mounted). */
const PREFERENCES_SECTION = {
  key: 'preferences',
  labelKey: 'settings.nav.preferences',
  icon: Languages,
} as const satisfies { key: string; labelKey: MessageKey; icon: LucideIcon };

export type SettingsSection =
  | (typeof SECTIONS)[number]['key']
  | (typeof CODING_SECTION)['key']
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

  const recovery = useNativeRecovery();

  // THR-387 S1b: during a native recovery state the target-backed sections
  // show the recovery panel inside Settings (panels stay mounted but hidden,
  // so drafts survive), while the sub-nav and the client-only Preferences
  // section stay usable. Without a recovery state the gate is unchanged.
  const gated = (panel: ReactNode): JSX.Element => (
    <>
      {!recovery && settingsQuery.isLoading && <div className="text-text-secondary flex-1 p-8 text-sm">{t('settings.page.loading')}</div>}
      {!recovery && settingsQuery.isError && (
        <div className="text-feedback-danger flex-1 p-8 text-sm">
          {t('settings.page.loadError')}
          {settingsQuery.error?.message && <> {settingsQuery.error.message}</>}
        </div>
      )}
      {(settingsQuery.data || recovery) && (
        <SettingsContent>
          <NativeRecoveryFence>{settingsQuery.data && panel}</NativeRecoveryFence>
        </SettingsContent>
      )}
    </>
  );
  const fenced = (element: ReactNode): JSX.Element => (
    <SettingsContent>
      <NativeRecoveryFence>{element}</NativeRecoveryFence>
    </SettingsContent>
  );

  return (
    <div className="bg-surface-canvas h-full">
      <ContentWrap className="max-w-content-narrow">
        <PageTitle className="mb-6" title={t('settings.page.title')} />

        {/* Panels are keyed by org (as the desktop Settings panes are): an org
            change after Discard consent never carries a draft into another org. */}
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
          <Route
            path="coding"
            element={fenced(<CodingPanel key={slug} />)}
          />
          <Route path="daemon-capacity" element={gated(<DaemonCapacityPanel key={slug} />)} />
          <Route path="organization" element={gated(settingsQuery.data && <OrganizationPanel key={slug} org={settingsQuery.data.org} />)} />
          <Route path="executors" element={gated(<ExecutorsPanel key={slug} />)} />
          <Route path="*" element={<Navigate to={`/orgs/${slug}/settings/daemon-capacity`} replace />} />
        </Routes>
      </ContentWrap>
    </div>
  );
}

/**
 * THR-387 S1b: org-less Settings at /settings/* for a native recovery state
 * met before any org is known (startup on `/`). It exists only during
 * recovery: Preferences is client-only (no API request), and the
 * target-backed sections show the recovery panel and mount nothing. Reached
 * without a recovery state (ordinary browser mode, direct URL) it renders the
 * caller's not-found page, exactly as the unmatched URL did before. When an
 * explicit Retry/Recheck clears the state it returns to `/`, which routes to
 * the first org or shows the recovery panel again.
 */
export function RecoverySettingsPage({ notFound }: { notFound: ReactNode }): JSX.Element {
  const recovery = useNativeRecovery();
  const { t } = useTranslation();
  // Latched once a recovery state is observed while mounted, at mount or
  // later. On a cold start, reload or direct URL the startup bootstrap meets
  // the native answer only after the first render, so a mount-time latch
  // would stay not-found for good. Updating state during render (not in an
  // effect) switches in the same commit. Observing nothing here (no query
  // observer) keeps Preferences entry free of requests.
  const [seenRecovery, setSeenRecovery] = useState(() => recovery !== null);
  if (recovery && !seenRecovery) setSeenRecovery(true);
  // Without a state it renders exactly as before: not found, fenced like any
  // other routed page (the AppShell fence is bypassed here).
  if (!seenRecovery) return <NativeRecoveryFence>{notFound}</NativeRecoveryFence>;
  if (!recovery) return <Navigate to="/" replace />;

  const base = '/settings';
  const fenced = (
    <SettingsContent base={base}>
      <NativeRecoveryFence>{null}</NativeRecoveryFence>
    </SettingsContent>
  );
  return (
    <div className="bg-surface-canvas h-full">
      <ContentWrap className="max-w-content-narrow">
        <PageTitle className="mb-6" title={t('settings.page.title')} />
        <Routes>
          <Route index element={<Navigate to={`${base}/preferences`} replace />} />
          <Route
            path="preferences"
            element={
              <SettingsContent base={base}>
                <PreferencesPanel />
              </SettingsContent>
            }
          />
          <Route path="daemon-capacity" element={fenced} />
          <Route path="organization" element={fenced} />
          <Route path="executors" element={fenced} />
          <Route path="coding" element={fenced} />
          <Route path="*" element={<Navigate to={`${base}/preferences`} replace />} />
        </Routes>
      </ContentWrap>
    </div>
  );
}

/** a-settings set-wrap: nav + content column capped at 1000, centered. */
function SettingsContent({ children, base }: { children: ReactNode; base?: string }): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return (
    <div
      className="mx-auto flex min-w-0 w-full flex-col sm:flex-row"
      data-testid="settings-content"
    >
      <SettingsSubNav base={base ?? `/orgs/${slug}/settings`} />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}

/**
 * SettingsSubNav — wrapped links below 640px; left rail at 640px and wider.
 * Each link navigates to <base>/:section (/orgs/:slug/settings, or the
 * org-less recovery /settings).
 * Each link carries a leading icon (per design ref `a-settings`).
 * Active link uses Pasture rounded-full pill style.
 */
function SettingsSubNav({ base }: { base: string }): JSX.Element {
  const { t } = useTranslation();
  const sections = [...SECTIONS, CODING_SECTION, PREFERENCES_SECTION];

  return (
    <aside className="border-border-default bg-surface-sunken w-full min-w-0 shrink-0 border-b p-3 sm:w-50 sm:border-r sm:border-b-0">
      <h3 className="text-overline text-text-secondary mb-2 tracking-wider uppercase">
        {t('settings.nav.heading')}
      </h3>
      <ul className="flex flex-wrap gap-0.5 sm:block sm:space-y-0.5">
        {sections.map((s) => (
          <li key={s.key} className="min-w-0">
            <NavLink
              to={`${base}/${s.key}`}
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

/**
 * THR-387 S6-W1: one Settings section as a pane of the desktop Settings
 * dialog. It renders the same panels and the same loading/error/recovery
 * gates as the full page, without the page title or sub-nav (the dialog owns
 * its own navigation). Runtime pages must render inside an `/orgs/:slug/*`
 * route with an `OrgProvider` (the sections read the org from the route);
 * Language (`preferences`) needs no org.
 */
export function SettingsPane({ section }: { section: SettingsSection }): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const settingsQuery = useSettings();
  const recovery = useNativeRecovery();
  const { t } = useTranslation();

  if (section === 'preferences') return <PreferencesPanel />;
  if (section === 'coding') {
    return <NativeRecoveryFence><CodingPanel key={slug} /></NativeRecoveryFence>;
  }
  if (!recovery && settingsQuery.isLoading) {
    return <div className="text-text-secondary p-8 text-sm">{t('settings.page.loading')}</div>;
  }
  // A failed REREAD (e.g. a Check result read) keeps the previously read
  // data, so the editors stay mounted with the error shown above them, as on
  // the full page (S6-W2a: an editor's draft and unresolved save result must
  // survive a read failure). Only a failed FIRST read shows the error alone.
  const loadError = !recovery && settingsQuery.isError && (
    <div className="text-feedback-danger p-8 text-sm">
      {t('settings.page.loadError')}
      {settingsQuery.error?.message && <> {settingsQuery.error.message}</>}
    </div>
  );
  if (loadError && !settingsQuery.data) return loadError;
  const data = settingsQuery.data;
  return (
    <NativeRecoveryFence>
      {loadError}
      {section === 'daemon-capacity' && data && <DaemonCapacityPanel />}
      {section === 'organization' && data && <OrganizationPanel org={data.org} />}
      {section === 'executors' && data && <ExecutorsPanel />}
    </NativeRecoveryFence>
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
import { CodingSection } from './sections/CodingSection';
import type { OrgSettings } from '@/lib/api/types';

function DaemonCapacityPanel(): JSX.Element {
  // The capacity section owns its own heading and description, matching the
  // approved desktop target. Repeating them here produced two stacked headers.
  return <div className="max-w-3xl p-4 sm:p-8"><DaemonCapacitySection /></div>;
}

function OrganizationPanel({ org }: { org: OrgSettings }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl p-4 sm:p-8">
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
    <div className="max-w-2xl p-4 sm:p-8">
      <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.panel.executors.title')}</h2>
      <p className="text-text-secondary mb-8 text-sm">
        {t('settings.panel.executors.description')}
      </p>
      <ExecutorsSection />
    </div>
  );
}

function CodingPanel(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl min-w-0 p-4 break-words sm:p-8">
      <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.panel.coding.title')}</h2>
      <p className="text-text-secondary mb-8 text-sm">
        {t('settings.panel.coding.description')}
      </p>
      <CodingSection />
    </div>
  );
}

function PreferencesPanel(): JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="max-w-2xl min-w-0 p-4 break-words sm:p-8">
      <h2 className="font-display mb-1 text-lg font-semibold">{t('settings.panel.preferences.title')}</h2>
      <p className="text-text-secondary mb-8 text-sm">
        {t('settings.panel.preferences.description')}
      </p>
      <PreferencesSection />
    </div>
  );
}
