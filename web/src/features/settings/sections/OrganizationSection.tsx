/**
 * OrganizationSection — editable org settings with real saves via PUT /settings/org.
 *
 * Clean⇄Dirty state machine:
 * - Every field change dirties the form
 * - Sticky save bar appears when dirty: Discard + Save changes
 * - Saving → Saving state (button disabled, spinner)
 * - Save success → Saved feedback, form re-syncs from response
 * - Save error → inline error message
 *
 * Live-vs-restart (derived from daemon config-loading code — see KB
 * org-config-reload-semantics):
 * - session_timeout_seconds: re-read per session spawn → LIVE ✓
 * - dreaming.*: re-read per scheduler tick (≤60s) → LIVE ✓
 * - threads.*: re-read per request/invocation → LIVE ✓
 *
 * ALL org fields are live-apply. None require restart.
 *
 * Unsaved-changes guard: prompts on nav-away via beforeunload.
 */
import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import { useUpdateOrgSettings } from '@/hooks/settings';
import { useAgentsList } from '@/hooks/agents';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import { RecipientsInput } from '@/design-system/patterns/RecipientsInput';
import type { OrgSettings, OrgSettingsPatch } from '@/lib/api/types';

interface FieldState {
  timeout: string;
  dreamEnabled: boolean;
  dreamTime: string;
  dreamTz: string;
  dreamCatchUp: boolean;
  dreamMode: string;
  dreamInclude: string;
  dreamExclude: string;
  threadsEnabled: boolean;
  threadsCap: string;
  threadsTimeout: string;
}

type SaveState =
  | { phase: 'idle' }
  | { phase: 'saving' }
  | { phase: 'saved' }
  | { phase: 'error'; message: string };

function buildFieldState(org: OrgSettings): FieldState {
  return {
    timeout: org.session_timeout_seconds === null ? '' : String(org.session_timeout_seconds),
    dreamEnabled: org.dreaming.enabled,
    dreamTime: org.dreaming.schedule.time,
    dreamTz: org.dreaming.schedule.timezone,
    dreamCatchUp: org.dreaming.catch_up_on_startup,
    dreamMode: org.dreaming.agents.mode,
    dreamInclude: org.dreaming.agents.include.join(', '),
    dreamExclude: org.dreaming.agents.exclude.join(', '),
    threadsEnabled: org.threads.enabled,
    threadsCap: String(org.threads.default_turn_cap),
    threadsTimeout:
      org.threads.invocation_timeout_seconds === null
        ? ''
        : String(org.threads.invocation_timeout_seconds),
  };
}

function fieldsEqual(a: FieldState, b: FieldState): boolean {
  return (
    a.timeout === b.timeout &&
    a.dreamEnabled === b.dreamEnabled &&
    a.dreamTime === b.dreamTime &&
    a.dreamTz === b.dreamTz &&
    a.dreamCatchUp === b.dreamCatchUp &&
    a.dreamMode === b.dreamMode &&
    a.dreamInclude === b.dreamInclude &&
    a.dreamExclude === b.dreamExclude &&
    a.threadsEnabled === b.threadsEnabled &&
    a.threadsCap === b.threadsCap &&
    a.threadsTimeout === b.threadsTimeout
  );
}

interface Props {
  org: OrgSettings;
}

export function OrganizationSection({ org }: Props): JSX.Element {
  const [fields, setFields] = useState<FieldState>(() => buildFieldState(org));
  const [lastSaved, setLastSaved] = useState<FieldState>(() => buildFieldState(org));
  const [saveState, setSaveState] = useState<SaveState>({ phase: 'idle' });
  const mutation = useUpdateOrgSettings();
  const agentsQuery = useAgentsList();
  const agentsList = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );

  // Reset fields when org data changes externally
  const prevOrgRef = useRef(org);
  useEffect(() => {
    if (prevOrgRef.current !== org) {
      prevOrgRef.current = org;
      const fresh = buildFieldState(org);
      setFields(fresh);
      setLastSaved(fresh);
      setSaveState({ phase: 'idle' });
    }
  }, [org]);

  const dirty = !fieldsEqual(fields, lastSaved);

  const update = useCallback(
    <K extends keyof FieldState>(key: K, value: FieldState[K]) => {
      setFields((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const handleDiscard = useCallback(() => {
    setFields({ ...lastSaved });
    setSaveState({ phase: 'idle' });
  }, [lastSaved]);

  const buildPatch = useCallback((): OrgSettingsPatch => {
    const patch: OrgSettingsPatch = {};
    const parsedTimeout = fields.timeout.trim() ? Number(fields.timeout) : null;
    patch.session_timeout_seconds = parsedTimeout;

    patch.dreaming = {
      enabled: fields.dreamEnabled,
      schedule: { time: fields.dreamTime, timezone: fields.dreamTz },
      catch_up_on_startup: fields.dreamCatchUp,
      agents: {
        mode: fields.dreamMode,
        include: fields.dreamInclude ? fields.dreamInclude.split(/\s*,\s*/).filter(Boolean) : [],
        exclude: fields.dreamExclude ? fields.dreamExclude.split(/\s*,\s*/).filter(Boolean) : [],
      },
    };

    const parsedCap = Number(fields.threadsCap);
    const parsedThreadTimeout = fields.threadsTimeout.trim() ? Number(fields.threadsTimeout) : null;
    patch.threads = {
      enabled: fields.threadsEnabled,
      default_turn_cap: !isNaN(parsedCap) ? parsedCap : undefined,
      invocation_timeout_seconds: parsedThreadTimeout,
    };
    return patch;
  }, [fields]);

  const handleSave = useCallback(async () => {
    setSaveState({ phase: 'saving' });
    try {
      const patch = buildPatch();
      const data = await mutation.mutateAsync(patch);
      const fresh = buildFieldState(data.org);
      setFields(fresh);
      setLastSaved(fresh);
      setSaveState({ phase: 'saved' });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSaveState({ phase: 'error', message: msg });
    }
  }, [buildPatch, mutation]);

  // ⌘S shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 's') {
        e.preventDefault();
        if (dirty && saveState.phase !== 'saving') {
          handleSave();
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [dirty, saveState.phase, handleSave]);

  // Unsaved-changes guard on nav-away
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  return (
    <section>
      {saveState.phase === 'error' && (
        <div className="border-tier-red bg-feedback-danger/10 text-tier-red mb-4 rounded border p-3 text-sm">
          Save failed: {saveState.message}
        </div>
      )}
      {saveState.phase === 'saved' && (
        <div className="border-tier-green bg-feedback-success/10 text-tier-green mb-4 rounded border p-3 text-sm">
          Saved. Changes will take effect within ~1 minute.
        </div>
      )}

      {/* session timeout */}
      <h4 className="mb-2 text-sm font-medium">Session</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label="Session timeout (s)" badge="Applies live">
          <input
            type="number"
            min={1}
            value={fields.timeout}
            onChange={(e) => update('timeout', e.target.value)}
            placeholder="use system default"
            className="bg-bg-raised border-border text-fg w-32 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* dreaming */}
      <h4 className="mb-2 text-sm font-medium">Dreaming</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label="Enabled" badge="Applies live">
          <BooleanToggle value={fields.dreamEnabled} onChange={(v) => update('dreamEnabled', v)} />
        </EditableRow>
        <EditableRow label="Schedule time" badge="Applies live">
          <input
            type="text"
            value={fields.dreamTime}
            onChange={(e) => update('dreamTime', e.target.value)}
            placeholder="HH:MM"
            className="bg-bg-raised border-border text-fg w-24 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label="Schedule timezone" badge="Applies live">
          <input
            type="text"
            value={fields.dreamTz}
            onChange={(e) => update('dreamTz', e.target.value)}
            placeholder="UTC"
            className="bg-bg-raised border-border text-fg w-48 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label="Catch up on startup" badge="Applies live">
          <BooleanToggle
            value={fields.dreamCatchUp}
            onChange={(v) => update('dreamCatchUp', v)}
          />
        </EditableRow>
        <EditableRow label="Agent mode" badge="Applies live">
          <Select value={fields.dreamMode} onValueChange={(v) => update('dreamMode', v)}>
            <SelectTrigger className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">all</SelectItem>
              <SelectItem value="whitelist">whitelist</SelectItem>
            </SelectContent>
          </Select>
        </EditableRow>
        <EditableRow label="Included agents" badge="Applies live">
          <RecipientsInput
            value={fields.dreamInclude}
            onChange={(next) => update('dreamInclude', next)}
            agents={agentsList}
            placeholder="add agents…"
            className="bg-bg-raised border-border text-fg w-56 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label="Excluded agents" badge="Applies live">
          <RecipientsInput
            value={fields.dreamExclude}
            onChange={(next) => update('dreamExclude', next)}
            agents={agentsList}
            placeholder="add agents…"
            className="bg-bg-raised border-border text-fg w-56 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* threads */}
      <h4 className="mb-2 text-sm font-medium">Threads</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label="Enabled" badge="Applies live">
          <BooleanToggle
            value={fields.threadsEnabled}
            onChange={(v) => update('threadsEnabled', v)}
          />
        </EditableRow>
        <EditableRow label="Default turn cap" badge="Applies live">
          <input
            type="number"
            min={1}
            value={fields.threadsCap}
            onChange={(e) => update('threadsCap', e.target.value)}
            className="bg-bg-raised border-border text-fg w-28 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label="Invocation timeout (s)" badge="Applies live">
          <input
            type="number"
            min={1}
            value={fields.threadsTimeout}
            onChange={(e) => update('threadsTimeout', e.target.value)}
            placeholder="none"
            className="bg-bg-raised border-border text-fg w-28 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* Sticky save bar */}
      {dirty && (
        <div className="border-border bg-bg-subtle sticky bottom-0 -mx-6 mt-6 -mb-6 flex items-center gap-3 border-t px-6 py-3">
          <button
            type="button"
            onClick={handleSave}
            disabled={saveState.phase === 'saving'}
            className="bg-accent text-accent-fg hover:bg-accent-hover rounded px-4 py-1.5 text-sm font-medium transition-colors disabled:opacity-50"
          >
            {saveState.phase === 'saving' ? 'Saving…' : 'Save changes'}
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={saveState.phase === 'saving'}
            className="text-fg-muted hover:text-fg rounded px-4 py-1.5 text-sm transition-colors disabled:opacity-50"
          >
            Discard
          </button>
          <span className="text-fg-subtle ml-auto text-xs">
            ⌘S to save
          </span>
        </div>
      )}
    </section>
  );
}

// ----------------------------------------------------------------
// Shared components
// ----------------------------------------------------------------

function EditableRow({
  label,
  badge,
  children,
}: {
  label: string;
  badge?: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between px-3 py-2 text-sm">
      <span className="text-fg-muted">{label}</span>
      <span className="flex items-center gap-2">
        {children}
        {badge && (
          <span className="bg-bg-raised text-tier-green rounded px-1.5 py-0.5 text-xs font-medium">
            {badge}
          </span>
        )}
      </span>
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
