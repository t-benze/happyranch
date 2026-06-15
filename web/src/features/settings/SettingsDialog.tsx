/**
 * Settings dialog — System (read-only) + Org (editable in Phase 2).
 *
 * Opened from a button in the TopBar. Renders two sections:
 * - System: daemon-wide settings with restart-required badges (read-only)
 * - Org: org-level settings with editable forms (Phase 2)
 *
 * NO agents, NO Feishu.
 */
import { Settings } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { useSettings, useUpdateOrgSettings } from '@/hooks/settings';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import type {
  SystemSettings,
  OrgSettings,
  OrgSettingsPatch,
} from '@/lib/api/types';

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
            <EditableOrgSection org={q.data.org} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ----------------------------------------------------------------
// System section (read-only)
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
// Editable Org section (Phase 2)
// ----------------------------------------------------------------

function EditableOrgSection({
  org,
}: {
  org: OrgSettings;
}): JSX.Element {
  const mutation = useUpdateOrgSettings();

  // Local form state — initialised from the current org snapshot
  const [timeout, setTimeout_] = useState(
    org.session_timeout_seconds === null ? '' : String(org.session_timeout_seconds),
  );
  const [dreamEnabled, setDreamEnabled] = useState(org.dreaming.enabled);
  const [dreamTime, setDreamTime] = useState(org.dreaming.schedule.time);
  const [dreamTz, setDreamTz] = useState(org.dreaming.schedule.timezone);
  const [dreamCatchUp, setDreamCatchUp] = useState(org.dreaming.catch_up_on_startup);
  const [dreamMode, setDreamMode] = useState(org.dreaming.agents.mode);
  const [dreamInclude, setDreamInclude] = useState(org.dreaming.agents.include.join(', '));
  const [dreamExclude, setDreamExclude] = useState(org.dreaming.agents.exclude.join(', '));
  const [threadsEnabled, setThreadsEnabled] = useState(org.threads.enabled);
  const [threadsCap, setThreadsCap] = useState(String(org.threads.default_turn_cap));
  const [threadsTimeout, setThreadsTimeout] = useState(
    org.threads.invocation_timeout_seconds === null ? '' : String(org.threads.invocation_timeout_seconds),
  );

  const [feedback, setFeedback] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  // Reset form when the underlying data changes (dialog re-open)
  const [prevOrg, setPrevOrg] = useState(org);
  if (org !== prevOrg) {
    setPrevOrg(org);
    setTimeout_(org.session_timeout_seconds === null ? '' : String(org.session_timeout_seconds));
    setDreamEnabled(org.dreaming.enabled);
    setDreamTime(org.dreaming.schedule.time);
    setDreamTz(org.dreaming.schedule.timezone);
    setDreamCatchUp(org.dreaming.catch_up_on_startup);
    setDreamMode(org.dreaming.agents.mode);
    setDreamInclude(org.dreaming.agents.include.join(', '));
    setDreamExclude(org.dreaming.agents.exclude.join(', '));
    setThreadsEnabled(org.threads.enabled);
    setThreadsCap(String(org.threads.default_turn_cap));
    setThreadsTimeout(
      org.threads.invocation_timeout_seconds === null ? '' : String(org.threads.invocation_timeout_seconds),
    );
    setFeedback(null);
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFeedback(null);

    const patch: OrgSettingsPatch = {};

    // session_timeout_seconds
    const parsedTimeout = timeout.trim() ? Number(timeout) : null;
    patch.session_timeout_seconds = parsedTimeout;

    // dreaming
    patch.dreaming = {
      enabled: dreamEnabled,
      schedule: { time: dreamTime, timezone: dreamTz },
      catch_up_on_startup: dreamCatchUp,
      agents: {
        mode: dreamMode,
        include: dreamInclude ? dreamInclude.split(/\s*,\s*/).filter(Boolean) : [],
        exclude: dreamExclude ? dreamExclude.split(/\s*,\s*/).filter(Boolean) : [],
      },
    };

    // threads
    const parsedCap = Number(threadsCap);
    const parsedThreadTimeout = threadsTimeout.trim() ? Number(threadsTimeout) : null;
    patch.threads = {
      enabled: threadsEnabled,
      default_turn_cap: !isNaN(parsedCap) ? parsedCap : undefined,
      invocation_timeout_seconds: parsedThreadTimeout,
    };

    try {
      const data = await mutation.mutateAsync(patch);
      setFeedback({ kind: 'ok', msg: 'Saved.' });
      // Re-init local state from response
      setTimeout_(data.org.session_timeout_seconds === null ? '' : String(data.org.session_timeout_seconds));
      setDreamEnabled(data.org.dreaming.enabled);
      setDreamTime(data.org.dreaming.schedule.time);
      setDreamTz(data.org.dreaming.schedule.timezone);
      setDreamCatchUp(data.org.dreaming.catch_up_on_startup);
      setDreamMode(data.org.dreaming.agents.mode);
      setDreamInclude(data.org.dreaming.agents.include.join(', '));
      setDreamExclude(data.org.dreaming.agents.exclude.join(', '));
      setThreadsEnabled(data.org.threads.enabled);
      setThreadsCap(String(data.org.threads.default_turn_cap));
      setThreadsTimeout(
        data.org.threads.invocation_timeout_seconds === null ? '' : String(data.org.threads.invocation_timeout_seconds),
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setFeedback({ kind: 'err', msg });
    }
  };

  return (
    <section>
      <h3 className="text-lg font-semibold mb-2">Org</h3>
      <form onSubmit={handleSubmit} className="space-y-4">
        {/* session timeout */}
        <div className="border-border divide-border divide-y rounded-md border">
          <EditableRow label="Session timeout (s)">
            <input
              type="number"
              min={1}
              value={timeout}
              onChange={(e) => setTimeout_(e.target.value)}
              placeholder="use system default"
              className="bg-bg-raised border-border text-fg w-32 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
        </div>

        {/* dreaming */}
        <h4 className="text-base font-medium">Dreaming</h4>
        <div className="border-border divide-border divide-y rounded-md border">
          <EditableRow label="Enabled">
            <BooleanToggle value={dreamEnabled} onChange={setDreamEnabled} />
          </EditableRow>
          <EditableRow label="Schedule time">
            <input
              type="text"
              value={dreamTime}
              onChange={(e) => setDreamTime(e.target.value)}
              placeholder="HH:MM"
              className="bg-bg-raised border-border text-fg w-24 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
          <EditableRow label="Schedule timezone">
            <input
              type="text"
              value={dreamTz}
              onChange={(e) => setDreamTz(e.target.value)}
              placeholder="UTC"
              className="bg-bg-raised border-border text-fg w-48 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
          <EditableRow label="Catch up on startup">
            <BooleanToggle value={dreamCatchUp} onChange={setDreamCatchUp} />
          </EditableRow>
          <EditableRow label="Agent mode">
            <Select value={dreamMode} onValueChange={setDreamMode}>
              <SelectTrigger className="w-28">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">all</SelectItem>
                <SelectItem value="whitelist">whitelist</SelectItem>
              </SelectContent>
            </Select>
          </EditableRow>
          <EditableRow label="Included agents">
            <input
              type="text"
              value={dreamInclude}
              onChange={(e) => setDreamInclude(e.target.value)}
              placeholder="comma-separated"
              className="bg-bg-raised border-border text-fg w-48 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
          <EditableRow label="Excluded agents">
            <input
              type="text"
              value={dreamExclude}
              onChange={(e) => setDreamExclude(e.target.value)}
              placeholder="comma-separated"
              className="bg-bg-raised border-border text-fg w-48 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
        </div>

        {/* threads */}
        <h4 className="text-base font-medium">Threads</h4>
        <div className="border-border divide-border divide-y rounded-md border">
          <EditableRow label="Enabled">
            <BooleanToggle value={threadsEnabled} onChange={setThreadsEnabled} />
          </EditableRow>
          <EditableRow label="Default turn cap">
            <input
              type="number"
              min={1}
              value={threadsCap}
              onChange={(e) => setThreadsCap(e.target.value)}
              className="bg-bg-raised border-border text-fg w-28 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
          <EditableRow label="Invocation timeout (s)">
            <input
              type="number"
              min={1}
              value={threadsTimeout}
              onChange={(e) => setThreadsTimeout(e.target.value)}
              placeholder="none"
              className="bg-bg-raised border-border text-fg w-28 rounded border px-2 py-0.5 text-sm"
            />
          </EditableRow>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="bg-accent text-accent-fg hover:bg-accent-hover rounded px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {mutation.isPending ? 'Saving…' : 'Save'}
          </button>
          {feedback && (
            <span
              className={
                feedback.kind === 'ok' ? 'text-tier-green text-sm' : 'text-tier-red text-sm'
              }
            >
              {feedback.msg}
            </span>
          )}
        </div>

        <p className="text-fg-subtle text-xs">
          Changes apply on next read. Dreaming scheduler picks up changes
          within ~1 min. Comments in config.yaml are not preserved.
        </p>
      </form>
    </section>
  );
}

// ----------------------------------------------------------------
// Shared components
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

function EditableRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between px-3 py-2 text-sm">
      <span className="text-fg-muted">{label}</span>
      {children}
    </div>
  );
}

function BooleanToggle({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => onChange(!value)}
      className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        value ? 'bg-accent' : 'bg-bg-raised border-border border'
      }`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
          value ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
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
