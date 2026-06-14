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
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import type { SystemSettings, OrgSettings } from '@/lib/api/types';

const RESTART_REQUIRED_FIELDS = new Set([
  'claude_cli_path',
  'codex_cli_path',
  'opencode_cli_path',
  'pi_cli_path',
  'max_orchestration_steps',
  'queue_workers',
  'protocol_dir',
]);

function restartRequired(fieldName: string): boolean {
  return RESTART_REQUIRED_FIELDS.has(fieldName);
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsDialog({ open, onOpenChange }: Props): JSX.Element {
  const q = useSettings();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto">
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
  const rows: [string, string | number][] = [
    ['Claude CLI path', sys.claude_cli_path],
    ['Codex CLI path', sys.codex_cli_path],
    ['OpenCode CLI path', sys.opencode_cli_path],
    ['Pi CLI path', sys.pi_cli_path],
    ['Session timeout (s)', sys.session_timeout_seconds],
    ['Max orchestration steps', sys.max_orchestration_steps],
    ['Queue workers', sys.queue_workers],
    ['Protocol dir', sys.protocol_dir],
  ];

  return (
    <section>
      <h3 className="text-lg font-semibold mb-2">System</h3>
      <div className="border-border divide-border divide-y rounded-md border">
        {rows.map(([label, value]) => (
          <SettingsRow
            key={label}
            label={label}
            value={String(value)}
            badge={
              restartRequired(labelToField(label)) ? 'Restart required' : undefined
            }
          />
        ))}
      </div>
    </section>
  );
}

function labelToField(label: string): string {
  switch (label) {
    case 'Claude CLI path': return 'claude_cli_path';
    case 'Codex CLI path': return 'codex_cli_path';
    case 'OpenCode CLI path': return 'opencode_cli_path';
    case 'Pi CLI path': return 'pi_cli_path';
    case 'Session timeout (s)': return 'session_timeout_seconds';
    case 'Max orchestration steps': return 'max_orchestration_steps';
    case 'Queue workers': return 'queue_workers';
    case 'Protocol dir': return 'protocol_dir';
    default: return '';
  }
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
