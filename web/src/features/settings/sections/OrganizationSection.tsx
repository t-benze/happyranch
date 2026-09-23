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
import { forwardRef, useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { EligibilityEditorDialog } from '@/shared/work-hours/EligibilityEditorDialog';
import { SavedBanner } from '@/shared/work-hours/SavedBanner';
import { extractServerErrors } from '@/shared/work-hours/extractServerErrors';
import type { OrgSettings, OrgSettingsPatch } from '@/lib/api/types';
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
import { useParams } from 'react-router-dom';
import { useTranslation } from '@/hooks/i18n';

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
    threadsTimeout:
      org.threads.invocation_timeout_seconds === null
        ? ''
        : String(org.threads.invocation_timeout_seconds),
  };
}

/**
 * Filter a comma-separated agent-name string to only tokens present in the
 * given roster. Non-roster tokens are silently dropped. Used at the save
 * boundary so that actively-typed (uncommitted) non-roster names never reach
 * the server, regardless of whether the user typed a trailing comma.
 */
function rosterTokens(raw: string, roster: Set<string>): string {
  if (!raw) return raw;
  return raw
    .split(/\s*,\s*/)
    .filter((t) => t === '' || roster.has(t))
    .join(', ');
}

interface Props {
  org: OrgSettings;
}

export function OrganizationSection({ org }: Props): JSX.Element {
  const [fields, setFields] = useState<FieldState>(() => buildFieldState(org));
  const [lastSaved, setLastSaved] = useState<FieldState>(() => buildFieldState(org));
  const [saveState, setSaveState] = useState<SaveState>({ phase: 'idle' });
  const { t, render } = useTranslation();
  const mutation = useUpdateOrgSettings();
  const agentsQuery = useAgentsList();
  const agentsList = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );

  // ── Operating controls (work-hours enablement + eligibility) ──
  const { slug: orgSlug } = useParams<{ slug: string }>();
  const wh = org.working_hours;
  const allAgentNames = useMemo(
    () => agentsList.map((a) => a.name),
    [agentsList],
  );
  const [confirmDisable, setConfirmDisable] = useState(false);
  const workHoursToggleRef = useRef<HTMLButtonElement>(null);
  const [editEligibility, setEditEligibility] = useState(false);
  // Semantic status, not rendered copy: the banner is translated at render so
  // an already-visible banner follows a locale switch without a resave.
  const [whSaved, setWhSaved] = useState(false);
  const [whError, setWhError] = useState<string | null>(null);

  const closeConfirmDisable = useCallback(() => {
    setConfirmDisable(false);
    workHoursToggleRef.current?.focus();
  }, []);

  async function setEnabled(next: boolean) {
    setWhError(null);
    try {
      await mutation.mutateAsync({ working_hours: { enabled: next } });
      setWhSaved(true);
    } catch (err: unknown) {
      setWhError(extractServerErrors(err).join('; '));
    }
  }

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

  const rosterNames = useMemo(
    () => new Set(agentsList.map((a) => a.name)),
    [agentsList],
  );

  const dirty = useMemo(() => {
    const filteredInclude = rosterTokens(fields.dreamInclude, rosterNames);
    const filteredExclude = rosterTokens(fields.dreamExclude, rosterNames);
    const savedInclude = rosterTokens(lastSaved.dreamInclude, rosterNames);
    const savedExclude = rosterTokens(lastSaved.dreamExclude, rosterNames);
    return (
      fields.timeout !== lastSaved.timeout ||
      fields.dreamEnabled !== lastSaved.dreamEnabled ||
      fields.dreamTime !== lastSaved.dreamTime ||
      fields.dreamTz !== lastSaved.dreamTz ||
      fields.dreamCatchUp !== lastSaved.dreamCatchUp ||
      fields.dreamMode !== lastSaved.dreamMode ||
      filteredInclude !== savedInclude ||
      filteredExclude !== savedExclude ||
      fields.threadsEnabled !== lastSaved.threadsEnabled ||
      fields.threadsTimeout !== lastSaved.threadsTimeout
    );
  }, [fields, lastSaved, rosterNames]);

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
        include: fields.dreamInclude
          ? rosterTokens(fields.dreamInclude, rosterNames).split(/\s*,\s*/).filter(Boolean)
          : [],
        exclude: fields.dreamExclude
          ? rosterTokens(fields.dreamExclude, rosterNames).split(/\s*,\s*/).filter(Boolean)
          : [],
      },
    };

    const parsedThreadTimeout = fields.threadsTimeout.trim() ? Number(fields.threadsTimeout) : null;
    patch.threads = {
      enabled: fields.threadsEnabled,
      invocation_timeout_seconds: parsedThreadTimeout,
    };
    return patch;
  }, [fields, rosterNames]);

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
          {t('settings.organization.saveFailed', { detail: saveState.message })}
        </div>
      )}
      {saveState.phase === 'saved' && (
        <div className="border-tier-green bg-feedback-success/10 text-tier-green mb-4 rounded border p-3 text-sm">
          {t('settings.organization.saved')}
        </div>
      )}

      {/* session timeout */}
      <h4 className="mb-2 text-sm font-medium">{t('settings.organization.session.heading')}</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label={t('settings.organization.session.timeout')} badge={t('settings.organization.badge.live')}>
          <input
            type="number"
            min={1}
            value={fields.timeout}
            onChange={(e) => update('timeout', e.target.value)}
            placeholder={t('settings.organization.session.timeoutPlaceholder')}
            className="bg-bg-raised border-border text-fg w-32 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* dreaming */}
      <h4 className="mb-2 text-sm font-medium">{t('settings.organization.dreaming.heading')}</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label={t('settings.organization.enabled')} badge={t('settings.organization.badge.live')}>
          <BooleanToggle value={fields.dreamEnabled} onChange={(v) => update('dreamEnabled', v)} />
        </EditableRow>
        <EditableRow label={t('settings.organization.dreaming.scheduleTime')} badge={t('settings.organization.badge.live')}>
          <input
            type="text"
            value={fields.dreamTime}
            onChange={(e) => update('dreamTime', e.target.value)}
            placeholder="HH:MM"
            className="bg-bg-raised border-border text-fg w-24 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label={t('settings.organization.dreaming.scheduleTimezone')} badge={t('settings.organization.badge.live')}>
          <input
            type="text"
            value={fields.dreamTz}
            onChange={(e) => update('dreamTz', e.target.value)}
            placeholder="UTC"
            className="bg-bg-raised border-border text-fg w-48 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label={t('settings.organization.dreaming.catchUp')} badge={t('settings.organization.badge.live')}>
          <BooleanToggle
            value={fields.dreamCatchUp}
            onChange={(v) => update('dreamCatchUp', v)}
          />
        </EditableRow>
        <EditableRow label={t('settings.organization.dreaming.agentMode')} badge={t('settings.organization.badge.live')}>
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
        <EditableRow label={t('settings.organization.dreaming.included')} badge={t('settings.organization.badge.live')}>
          <RecipientsInput
            value={fields.dreamInclude}
            onChange={(next) => update('dreamInclude', next)}
            agents={agentsList}
            restrictToOptions
            placeholder={t('settings.organization.dreaming.addAgentsPlaceholder')}
            className="bg-bg-raised border-border text-fg w-56 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
        <EditableRow label={t('settings.organization.dreaming.excluded')} badge={t('settings.organization.badge.live')}>
          <RecipientsInput
            value={fields.dreamExclude}
            onChange={(next) => update('dreamExclude', next)}
            agents={agentsList}
            restrictToOptions
            placeholder={t('settings.organization.dreaming.addAgentsPlaceholder')}
            className="bg-bg-raised border-border text-fg w-56 rounded border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* threads */}
      <h4 className="mb-2 text-sm font-medium">{t('settings.organization.threads.heading')}</h4>
      <div className="border-border divide-border mb-4 divide-y rounded-md border">
        <EditableRow label={t('settings.organization.enabled')} badge={t('settings.organization.badge.live')}>
          <BooleanToggle
            value={fields.threadsEnabled}
            onChange={(v) => update('threadsEnabled', v)}
          />
        </EditableRow>
        <EditableRow label={t('settings.organization.threads.invocationTimeout')} badge={t('settings.organization.badge.live')}>
          <input
            type="number"
            min={1}
            value={fields.threadsTimeout}
            onChange={(e) => update('threadsTimeout', e.target.value)}
            placeholder={t('settings.organization.threads.timeoutPlaceholder')}
            className="bg-surface-sunken border-border-default text-text-primary w-28 rounded-lg border px-2 py-0.5 text-sm"
          />
        </EditableRow>
      </div>

      {/* ── Operating controls ── */}
      <h4 className="mb-2 text-sm font-medium">{t('settings.organization.operating.heading')}</h4>
      <p className="text-text-secondary mb-3 text-xs">
        {render('settings.organization.operating.description', {
          link: (
            <a
              href={`/orgs/${orgSlug}/work-hours`}
              className="text-accent-text hover:underline"
            >
              {t('settings.organization.operating.workHoursLink')}
            </a>
          ),
        })}
      </p>

      {whSaved && (
        <SavedBanner message={t('settings.organization.workHoursSaved')} />
      )}
      {whError && (
        <div
          role="alert"
          className="border-tier-red bg-feedback-danger/10 text-tier-red mb-4 rounded border p-3 text-sm"
        >
          {whError}
        </div>
      )}

      <div className="border-border divide-border mb-4 divide-y rounded-md border" data-testid="operating-controls">
        <EditableRow label={t('settings.organization.operating.workHours')} labelId="work-hours-switch-label" badge={t('settings.organization.badge.live')}>
          <div className="flex items-center gap-2">
            <BooleanToggle
              ref={workHoursToggleRef}
              aria-labelledby="work-hours-switch-label"
              value={wh?.enabled ?? false}
              onChange={(v) => {
                if (v) {
                  setEnabled(true);
                } else {
                  setConfirmDisable(true);
                }
              }}
            />
            <span className="text-text-muted text-xs" aria-hidden="true">
              {wh?.enabled ? t('settings.organization.operating.on') : t('settings.organization.operating.off')}
            </span>
          </div>
        </EditableRow>
        <EditableRow label={t('settings.organization.operating.eligibility')}>
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-xs">
              {wh?.agents?.mode === 'whitelist'
                ? t('settings.organization.operating.whitelist', { count: wh.agents.include.length })
                : t('settings.organization.operating.allAgents')}
              {wh?.agents?.exclude.length
                ? t('settings.organization.operating.excluded', { count: wh.agents.exclude.length })
                : ''}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditEligibility(true)}
            >
              {t('settings.organization.operating.editEligibility')}
            </Button>
          </div>
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
            {saveState.phase === 'saving' ? t('settings.organization.saveBar.saving') : t('settings.organization.saveBar.save')}
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={saveState.phase === 'saving'}
            className="text-fg-muted hover:text-fg rounded px-4 py-1.5 text-sm transition-colors disabled:opacity-50"
          >
            {t('settings.organization.saveBar.discard')}
          </button>
          <span className="text-fg-subtle ml-auto text-xs">
            {t('settings.organization.saveBar.shortcut')}
          </span>
        </div>
      )}

      {/* Confirm-before-disable the global feature switch. */}
      <Dialog
        open={confirmDisable}
        onOpenChange={(open) => {
          if (!open) closeConfirmDisable();
        }}
      >
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            workHoursToggleRef.current?.focus();
          }}
        >
          <DialogHeader>
            <DialogTitle>{t('settings.organization.disableDialog.title')}</DialogTitle>
            <DialogDescription>
              {t('settings.organization.disableDialog.description')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={closeConfirmDisable}>
              {t('settings.organization.disableDialog.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                closeConfirmDisable();
                void setEnabled(false);
              }}
            >
              {t('settings.organization.disableDialog.confirm')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Eligibility editor — shared dialog from work-hours-config. */}
      {editEligibility && wh && (
        <EligibilityEditorDialog
          open={editEligibility}
          onOpenChange={setEditEligibility}
          wh={wh}
          allAgents={allAgentNames}
          onSaved={() => {
            setWhSaved(true);
          }}
        />
      )}

    </section>
  );
}

// ----------------------------------------------------------------
// Shared components
// ----------------------------------------------------------------

function EditableRow({
  label,
  labelId,
  badge,
  children,
}: {
  label: string;
  labelId?: string;
  badge?: string;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between px-3 py-2 text-sm">
      <span id={labelId} className="text-fg-muted">{label}</span>
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

const BooleanToggle = forwardRef<HTMLButtonElement, {
  value: boolean;
  onChange: (v: boolean) => void;
  'aria-labelledby'?: string;
}>(function BooleanToggle({
  value,
  onChange,
  'aria-labelledby': ariaLabelledBy,
}, ref): JSX.Element {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={value}
      aria-labelledby={ariaLabelledBy}
      onClick={(e) => {
        // Ignore keyboard-synthesized clicks; keyboard activation is handled
        // in onKeyDown so Enter/Space toggle exactly once.
        if (e.detail === 0) return;
        onChange(!value);
      }}
      onKeyDown={(e) => {
        if (
          e.key === 'Enter' ||
          e.key === ' ' ||
          e.key === 'Space' ||
          e.key === 'Spacebar'
        ) {
          e.preventDefault();
          onChange(!value);
        }
      }}
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
});
