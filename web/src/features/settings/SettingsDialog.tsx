/**
 * Settings dialog — read-only System + Org settings (Phase 1).
 *
 * Opened from a button in the TopBar. Renders two sections:
 * - System: daemon-wide settings with restart-required badges
 * - Org: org-level settings (session timeout, dreaming, threads)
 *
 * NO agents, NO Feishu, NO editable fields (Phase 2 will add PUT /settings/org).
 */
import { Settings } from 'lucide-react';
import { useSettings } from '@/hooks/settings';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import type { SystemSettings, OrgSettings } from '@/lib/api/types';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsDialog({ open, onOpenChange }: Props): JSX.Element {
  const q = useSettings();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
        </DialogHeader>

        {q.isLoading && (
          <p className="text-fg-muted text-sm py-4">Loading settings…</p>
        )}
        {q.isError && (
          <p className="text-tier-red text-sm py-4">
            Could not load settings.
            {q.error?.message && <> {q.error.message}</>}
          </p>
        )}
        {q.data && (
          <div className="space-y-6 mt-2">
            <SystemSection sys={q.data.system} />
            <OrgSection org={q.data.org} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ----------------------------------------------------------------
// System section
// ----------------------------------------------------------------

function SystemSection({ sys }: { sys: SystemSettings }): JSX.Element {
  const rows: { label: string; entry: SystemSettings[keyof SystemSettings] }[] = [
    { label: 'Claude CLI path', entry: sys.claude_cli_path },
    { label: 'Codex CLI path', entry: sys.codex_cli_path },
    { label: 'OpenCode CLI path', entry: sys.opencode_cli_path },
    { label: 'Pi CLI path', entry: sys.pi_cli_path },
    { label: 'Session timeout (s)', entry: sys.session_timeout_seconds },
    { label: 'Max orchestration steps', entry: sys.max_orchestration_steps },
    { label: 'Queue workers', entry: sys.queue_workers },
    { label: 'Protocol dir', entry: sys.protocol_dir },
  ];

  return (
    <section>
      <h3 className="text-lg font-semibold mb-2">System</h3>
      <div className="border-border divide-border divide-y rounded-md border">
        {rows.map(({ label, entry }) => (
          <SettingsRow
            key={label}
            label={label}
            value={String(entry.value)}
            badge={entry.restart_required ? 'Restart required' : undefined}
          />
        ))}
      </div>
    </section>
  );
}

// ----------------------------------------------------------------
// Org section
// ----------------------------------------------------------------

function OrgSection({ org }: { org: OrgSettings }): JSX.Element {
  return (
    <section>
      <h3 className="text-lg font-semibold mb-2">Org</h3>

      <div className="border-border divide-border divide-y rounded-md border">
        <SettingsRow
          label="Session timeout (s)"
          value={org.session_timeout_seconds === null ? '—' : String(org.session_timeout_seconds)}
          muted={org.session_timeout_seconds === null}
        />
      </div>

      <h4 className="text-base font-medium mt-4 mb-2">Dreaming</h4>
      <div className="border-border divide-border divide-y rounded-md border">
        <SettingsRow label="Enabled" value={org.dreaming.enabled ? 'Yes' : 'No'} />
        <SettingsRow label="Schedule time" value={org.dreaming.schedule.time} />
        <SettingsRow label="Schedule timezone" value={org.dreaming.schedule.timezone} />
        <SettingsRow label="Catch up on startup" value={org.dreaming.catch_up_on_startup ? 'Yes' : 'No'} />
        <SettingsRow label="Agent mode" value={org.dreaming.agents.mode} />
        <SettingsRow
          label="Included agents"
          value={org.dreaming.agents.include.length ? org.dreaming.agents.include.join(', ') : '—'}
          muted={!org.dreaming.agents.include.length}
        />
        <SettingsRow
          label="Excluded agents"
          value={org.dreaming.agents.exclude.length ? org.dreaming.agents.exclude.join(', ') : '—'}
          muted={!org.dreaming.agents.exclude.length}
        />
      </div>

      <h4 className="text-base font-medium mt-4 mb-2">Threads</h4>
      <div className="border-border divide-border divide-y rounded-md border">
        <SettingsRow label="Enabled" value={org.threads.enabled ? 'Yes' : 'No'} />
        <SettingsRow label="Default turn cap" value={String(org.threads.default_turn_cap)} />
        <SettingsRow
          label="Invocation timeout (s)"
          value={org.threads.invocation_timeout_seconds === null ? '—' : String(org.threads.invocation_timeout_seconds)}
          muted={org.threads.invocation_timeout_seconds === null}
        />
      </div>
    </section>
  );
}

// ----------------------------------------------------------------
// Shared row component
// ----------------------------------------------------------------

function SettingsRow({
  label,
  value,
  badge,
  muted,
}: {
  label: string;
  value: string;
  badge?: string;
  muted?: boolean;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between px-3 py-2 text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="flex items-center gap-2">
        <span className={muted ? 'text-fg-subtle' : 'text-fg'}>
          {value}
        </span>
        {badge && (
          <span className="bg-bg-raised text-accent text-xs rounded px-1.5 py-0.5 font-medium">
            {badge}
          </span>
        )}
      </span>
    </div>
  );
}

// ----------------------------------------------------------------
// TopBar trigger button
// ----------------------------------------------------------------

export function SettingsTriggerButton({
  onClick,
}: {
  onClick: () => void;
}): JSX.Element {
  const label = 'Settings';
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
    >
      <Settings size={16} aria-hidden="true" />
    </button>
  );
}
