/**
 * SettingsPage — full page (not dialog) with sticky left sub-nav + field panel.
 *
 * Sub-nav: Assistant · System · Organization · Agents · Executors · Usage.
 * Each sub-nav item routes to /orgs/:slug/settings/:section.
 * The Organization section has real saves via PUT /settings/org.
 *
 * Interaction acceptance (per PRD §4.11):
 * - iAC1: real bookmarkable route, sub-nav switches panels
 * - iAC2: per-field live-vs-restart badges match daemon behavior
 * - iAC3: agent-name inputs autocomplete from real roster
 * - iAC4: no field implies @mention routing (§A.2)
 */
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useParams,
} from 'react-router-dom';
import { useSettings } from '@/hooks/settings';
import { PageHeader } from '@/design-system/patterns/PageHeader';

const SECTIONS = [
  { key: 'assistant', label: 'Assistant' },
  { key: 'system', label: 'System' },
  { key: 'organization', label: 'Organization' },
  { key: 'agents', label: 'Agents' },
  { key: 'executors', label: 'Executors' },
  { key: 'usage', label: 'Usage' },
] as const;

export type SettingsSection = (typeof SECTIONS)[number]['key'];

/**
 * SettingsPage — the outer shell: left sub-nav + right panel outlet.
 *
 * Rendered by the router at /orgs/:slug/settings and
 * /orgs/:slug/settings/:section.
 */
export function SettingsPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const settingsQuery = useSettings();

  return (
    <div className="bg-surface-canvas flex h-full flex-col">
      <header className="border-border-default border-b p-4">
        <PageHeader
          title={<span className="font-display">Settings</span>}
          meta="Daemon and org configuration."
        />
      </header>

      {settingsQuery.isLoading && (
        <div className="text-text-secondary flex-1 p-6 text-sm">Loading settings…</div>
      )}
      {settingsQuery.isError && (
        <div className="text-feedback-danger flex-1 p-6 text-sm">
          Could not load settings.
          {settingsQuery.error?.message && <> {settingsQuery.error.message}</>}
        </div>
      )}

      {settingsQuery.data && (
        <div className="flex flex-1 overflow-hidden" data-testid="settings-content">
          <SettingsSubNav />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route index element={<Navigate to={`/orgs/${slug}/settings/assistant`} replace />} />
              <Route path="assistant" element={<AssistantPanel />} />
              <Route path="system" element={<SystemPanel sys={settingsQuery.data.system} />} />
              <Route
                path="organization"
                element={<OrganizationPanel org={settingsQuery.data.org} />}
              />
              <Route path="agents" element={<AgentsPanel />} />
              <Route path="executors" element={<ExecutorsPanel />} />
              <Route path="usage" element={<UsagePanel />} />
              <Route path="*" element={<Navigate to={`/orgs/${slug}/settings/assistant`} replace />} />
            </Routes>
          </main>
        </div>
      )}
    </div>
  );
}

/**
 * SettingsSubNav — sticky left rail of section links.
 * Each link navigates to /orgs/:slug/settings/:section.
 * Active link uses Pasture rounded-full pill style.
 */
function SettingsSubNav(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();

  return (
    <aside className="border-border-default bg-surface-sunken w-52 shrink-0 overflow-y-auto border-r p-3">
      <h3 className="text-overline text-text-secondary mb-2 tracking-wider uppercase">
        Configuration
      </h3>
      <ul className="space-y-0.5">
        {SECTIONS.map((s) => (
          <li key={s.key}>
            <NavLink
              to={`/orgs/${slug}/settings/${s.key}`}
              className={({ isActive }) =>
                `block w-full rounded-full px-3 py-1 text-left text-sm transition-colors ${
                  isActive
                    ? 'bg-accent-soft text-accent-text'
                    : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'
                }`
              }
            >
              {s.label}
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

import { AssistantSection } from './sections/AssistantSection';
import { SystemSection } from './sections/SystemSection';
import { OrganizationSection } from './sections/OrganizationSection';
import { AgentsSection } from './sections/AgentsSection';
import { ExecutorsSection } from './sections/ExecutorsSection';
import { UsageSection } from './sections/UsageSection';
import type { SystemSettings, OrgSettings } from '@/lib/api/types';

function AssistantPanel(): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">System Assistant</h2>
      <AssistantSection />
    </div>
  );
}

function SystemPanel({ sys }: { sys: SystemSettings }): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">System</h2>
      <p className="text-text-secondary mb-4 text-sm">
        Daemon-wide settings. These are read-only — changes require a restart and
        must be made in the daemon config file.
      </p>
      <SystemSection sys={sys} />
    </div>
  );
}

function OrganizationPanel({ org }: { org: OrgSettings }): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">Organization</h2>
      <p className="text-text-secondary mb-4 text-sm">
        Org-level settings. Changes apply live — the daemon hot-reloads them
        automatically.
      </p>
      <OrganizationSection org={org} />
    </div>
  );
}

function AgentsPanel(): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">Agents</h2>
      <AgentsSection />
    </div>
  );
}

function ExecutorsPanel(): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">Executors</h2>
      <ExecutorsSection />
    </div>
  );
}

function UsagePanel(): JSX.Element {
  return (
    <div className="max-w-2xl p-6">
      <h2 className="font-display mb-4 text-lg font-semibold">Usage</h2>
      <p className="text-text-secondary mb-4 text-sm">
        Token consumption across the org.
      </p>
      <UsageSection />
    </div>
  );
}
