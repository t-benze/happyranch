/**
 * S3 — reusable tier editor (Org default / Team default / Agent override).
 *
 * Mode-aware: windowed shows window + days; continuous shows interval-only.
 * For team/agent tiers, unset leaves show ghosted inherited values + a per-leaf
 * "reset to inherited" control that sends `null` on save.
 *
 * Save semantics: build a PARTIAL working_hours patch scoped to this tier and
 * PUT it. The SERVER validates (divides-24h, start<end, interval≤window,
 * window-completeness) — the client does FORMAT HINTS only. 422 errors are
 * surfaced in a blocking panel mapped from the server response.
 *
 * Impact preview: org/team saves first show "affects N agents" before writing.
 */
import { useMemo, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import { useUpdateOrgSettings } from '@/hooks/settings';
import type {
  WorkHoursLayer,
  WorkingHoursPatch,
  WorkingHoursSettings,
} from '@/lib/api/types';
import { ErrorPanel } from './components';
import { extractServerErrors } from './errors';
import {
  buildLayerPatch,
  type LayerDraft,
  reconcile,
} from './merge';
import { DAYS, ianaTimezones } from './constants';

export type Tier =
  | { kind: 'org' }
  | { kind: 'team'; team: string }
  | { kind: 'agent'; agent: string };

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tier: Tier;
  wh: WorkingHoursSettings;
  /** Agent→team map (for impact-preview scoping + inherited resolution). */
  agentTeam: Record<string, string | null>;
  allAgents: string[];
  onSaved: () => void;
}

const UNSET = '__unset__';

function tierLayer(wh: WorkingHoursSettings, tier: Tier): WorkHoursLayer | undefined {
  if (tier.kind === 'org') return wh.default;
  if (tier.kind === 'team') return wh.teams[tier.team];
  return wh.overrides[tier.agent];
}

/** Inherited (lower-precedence) effective value for a leaf, for ghosting. */
function inheritedFor(
  wh: WorkingHoursSettings,
  tier: Tier,
  agentTeam: Record<string, string | null>,
): {
  mode: string | null;
  start: string | null;
  end: string | null;
  timezone: string | null;
  interval: string | null;
  days: string[] | null;
  catchUp: boolean | null;
  sourceLabel: (leaf: string) => string;
} {
  // Org tier inherits nothing. Team inherits org. Agent inherits org+team.
  const lower: WorkingHoursSettings = {
    ...wh,
    teams: tier.kind === 'agent' ? wh.teams : {},
    overrides: {},
  };
  if (tier.kind === 'org') {
    lower.teams = {};
  }
  const teamName =
    tier.kind === 'agent'
      ? agentTeam[tier.agent] ?? null
      : tier.kind === 'team'
        ? null
        : null;
  // Reconcile a hypothetical agent against the lower tiers only.
  const probeAgent = tier.kind === 'agent' ? tier.agent : '__probe__';
  const rec = reconcile(
    { ...lower, overrides: {} },
    probeAgent,
    tier.kind === 'agent' ? teamName : null,
  );
  const get = (leaf: string) => rec.rows.find((r) => r.leaf === leaf)?.cell;
  const sourceLabel = (leaf: string): string => {
    const cell = get(leaf);
    if (!cell || cell.source === 'unset') return 'unset';
    if (cell.source === 'team') return `Team: ${teamName ?? ''}`;
    return 'Org default';
  };
  return {
    mode: (get('mode')?.effective as string | null) ?? null,
    start: (get('window.start')?.effective as string | null) ?? null,
    end: (get('window.end')?.effective as string | null) ?? null,
    timezone: (get('window.timezone')?.effective as string | null) ?? null,
    interval: (get('interval')?.effective as string | null) ?? null,
    days: (get('days')?.effective as string[] | null) ?? null,
    catchUp: (get('catch_up_on_startup')?.effective as boolean | null) ?? null,
    sourceLabel,
  };
}

export function TierEditorDialog({
  open,
  onOpenChange,
  tier,
  wh,
  agentTeam,
  allAgents,
  onSaved,
}: Props): JSX.Element {
  const mutation = useUpdateOrgSettings();
  const current = tierLayer(wh, tier);
  const showGhosts = tier.kind !== 'org';
  const inherited = useMemo(
    () => inheritedFor(wh, tier, agentTeam),
    [wh, tier, agentTeam],
  );
  const timezones = useMemo(() => ianaTimezones(), []);

  // Draft seeded from the tier's own raw layer (null = unset at this tier).
  const [mode, setMode] = useState<string | null>(current?.mode ?? null);
  const [start, setStart] = useState<string | null>(current?.window.start ?? null);
  const [end, setEnd] = useState<string | null>(current?.window.end ?? null);
  const [timezone, setTimezone] = useState<string | null>(
    current?.window.timezone ?? null,
  );
  const [interval, setInterval] = useState<string | null>(current?.interval ?? null);
  const [days, setDays] = useState<string[] | null>(current?.days ?? null);
  const [catchUp, setCatchUp] = useState<boolean | null>(
    current?.catch_up_on_startup ?? null,
  );
  const [errors, setErrors] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);

  const effectiveMode = mode ?? inherited.mode;
  const isContinuous = effectiveMode === 'continuous';

  const impactedAgents = useMemo(() => {
    if (tier.kind === 'org') {
      // Agents that don't override at all are affected by an org change.
      return allAgents.filter((a) => !wh.overrides[a]);
    }
    if (tier.kind === 'team') {
      return allAgents.filter(
        (a) => agentTeam[a] === tier.team && !wh.overrides[a],
      );
    }
    return [tier.agent];
  }, [tier, allAgents, wh.overrides, agentTeam]);

  const requiresConfirm = tier.kind !== 'agent';

  function buildPatch(): WorkingHoursPatch {
    // Only emit leaves the user touched relative to the current raw layer. To
    // keep it simple and predictable, emit ALL leaves of THIS tier draft so the
    // tier is written as the user sees it; unset leaves are sent as null
    // (reset-to-inherited).
    const draft: LayerDraft = {
      mode,
      interval: isContinuous ? interval : interval,
      catch_up_on_startup: catchUp,
      start: isContinuous ? null : start,
      end: isContinuous ? null : end,
      timezone,
      days: isContinuous ? null : days,
    };
    const layerPatch = buildLayerPatch(draft);
    if (tier.kind === 'org') return { default: layerPatch };
    if (tier.kind === 'team') return { teams: { [tier.team]: layerPatch } };
    return { overrides: { [tier.agent]: layerPatch } };
  }

  async function doSave() {
    setErrors([]);
    try {
      await mutation.mutateAsync({ working_hours: buildPatch() });
      setConfirming(false);
      onSaved();
      onOpenChange(false);
    } catch (err: unknown) {
      setConfirming(false);
      setErrors(extractServerErrors(err));
    }
  }

  function handleSaveClick() {
    if (requiresConfirm) {
      setConfirming(true);
    } else {
      void doSave();
    }
  }

  function toggleDay(day: string) {
    setDays((prev) => {
      const base = prev ?? [];
      return base.includes(day)
        ? base.filter((d) => d !== day)
        : [...base, day];
    });
  }

  const title =
    tier.kind === 'org'
      ? 'Edit org default'
      : tier.kind === 'team'
        ? `Edit team: ${tier.team}`
        : `Edit override — ${tier.agent}`;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Tier: {tier.kind.toUpperCase()}. The server validates the merged
            config on save — invalid edits are rejected and never written.
          </DialogDescription>
        </DialogHeader>

        {errors.length > 0 && <ErrorPanel errors={errors} />}

        {confirming ? (
          <div className="text-sm">
            <p className="text-text-primary">
              This change alters the effective schedule of{' '}
              <span className="font-semibold tabular-nums">
                {impactedAgents.length}
              </span>{' '}
              agent{impactedAgents.length !== 1 ? 's' : ''}:
            </p>
            <p className="text-text-muted mt-1 break-words">
              {impactedAgents.length > 0 ? impactedAgents.join(', ') : '(none)'}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {/* mode */}
            <Row
              label="mode"
              ghost={showGhosts && mode === null ? `inherited: ${inherited.mode ?? '—'} (${inherited.sourceLabel('mode')})` : undefined}
              onReset={showGhosts && mode !== null ? () => setMode(null) : undefined}
            >
              <Select
                value={mode ?? UNSET}
                onValueChange={(v) => setMode(v === UNSET ? null : v)}
              >
                <SelectTrigger className="w-40">
                  <SelectValue placeholder="inherited" />
                </SelectTrigger>
                <SelectContent>
                  {showGhosts && <SelectItem value={UNSET}>inherited</SelectItem>}
                  <SelectItem value="windowed">windowed</SelectItem>
                  <SelectItem value="continuous">continuous</SelectItem>
                </SelectContent>
              </Select>
            </Row>

            {!isContinuous && (
              <>
                <Row
                  label="window.start"
                  ghost={showGhosts && start === null ? `inherited: ${inherited.start ?? '—'} (${inherited.sourceLabel('window.start')})` : undefined}
                  onReset={showGhosts && start !== null ? () => setStart(null) : undefined}
                >
                  <Input
                    type="time"
                    className="w-32"
                    value={start ?? ''}
                    onChange={(e) => setStart(e.target.value || null)}
                  />
                </Row>
                <Row
                  label="window.end"
                  ghost={showGhosts && end === null ? `inherited: ${inherited.end ?? '—'} (${inherited.sourceLabel('window.end')})` : undefined}
                  onReset={showGhosts && end !== null ? () => setEnd(null) : undefined}
                >
                  <Input
                    type="time"
                    className="w-32"
                    value={end ?? ''}
                    onChange={(e) => setEnd(e.target.value || null)}
                  />
                </Row>
                <Row
                  label="days"
                  ghost={showGhosts && days === null ? `inherited: ${(inherited.days ?? []).join(',') || '—'} (${inherited.sourceLabel('days')})` : undefined}
                  onReset={showGhosts && days !== null ? () => setDays(null) : undefined}
                >
                  <div className="flex flex-wrap gap-1">
                    {DAYS.map((d) => {
                      const selected = (days ?? []).includes(d);
                      return (
                        <button
                          key={d}
                          type="button"
                          aria-pressed={selected}
                          onClick={() => toggleDay(d)}
                          className={`rounded px-2 py-0.5 text-xs font-medium ${
                            selected
                              ? 'bg-accent text-accent-fg'
                              : 'bg-bg-raised text-fg-muted border-border border'
                          }`}
                        >
                          {d}
                        </button>
                      );
                    })}
                  </div>
                </Row>
              </>
            )}

            <Row
              label="window.timezone"
              ghost={showGhosts && timezone === null ? `inherited: ${inherited.timezone ?? '—'} (${inherited.sourceLabel('window.timezone')})` : undefined}
              onReset={showGhosts && timezone !== null ? () => setTimezone(null) : undefined}
            >
              <Select
                value={timezone ?? UNSET}
                onValueChange={(v) => setTimezone(v === UNSET ? null : v)}
              >
                <SelectTrigger className="w-56">
                  <SelectValue placeholder="inherited" />
                </SelectTrigger>
                <SelectContent>
                  {showGhosts && <SelectItem value={UNSET}>inherited</SelectItem>}
                  {timezones.map((tz) => (
                    <SelectItem key={tz} value={tz}>
                      {tz}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Row>

            <Row
              label="interval"
              hint={
                isContinuous
                  ? 'divisor of 24h (server validates)'
                  : 'format like 2h / 30m (server validates ≤ window length)'
              }
              ghost={showGhosts && interval === null ? `inherited: ${inherited.interval ?? '—'} (${inherited.sourceLabel('interval')})` : undefined}
              onReset={showGhosts && interval !== null ? () => setInterval(null) : undefined}
            >
              {/* Free-form in BOTH modes — the server is the sole authority on
                  the divides-24h / interval≤window rules; the client only hints
                  the '2h / 30m' shape and surfaces the PUT 422 if it's bad. */}
              <Input
                type="text"
                className="w-32"
                placeholder="2h"
                value={interval ?? ''}
                onChange={(e) => setInterval(e.target.value || null)}
              />
            </Row>

            <Row
              label="catch_up_on_startup"
              ghost={showGhosts && catchUp === null ? `inherited: ${inherited.catchUp === null ? '—' : String(inherited.catchUp)} (${inherited.sourceLabel('catch_up_on_startup')})` : undefined}
              onReset={showGhosts && catchUp !== null ? () => setCatchUp(null) : undefined}
            >
              <button
                type="button"
                role="switch"
                aria-checked={catchUp ?? false}
                onClick={() => setCatchUp((p) => (p === null ? true : !p))}
                className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                  catchUp ? 'bg-accent' : 'bg-bg-raised border-border border'
                }`}
              >
                <span
                  className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                    catchUp ? 'translate-x-4' : 'translate-x-0.5'
                  }`}
                />
              </button>
            </Row>
          </div>
        )}

        <DialogFooter>
          {confirming ? (
            <>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Back
              </Button>
              <Button onClick={() => void doSave()} disabled={mutation.isPending}>
                {mutation.isPending ? 'Saving…' : 'Confirm & save'}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button onClick={handleSaveClick} disabled={mutation.isPending}>
                {mutation.isPending
                  ? 'Saving…'
                  : requiresConfirm
                    ? 'Review impact…'
                    : 'Save'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Row({
  label,
  hint,
  ghost,
  onReset,
  children,
}: {
  label: string;
  hint?: string;
  ghost?: string;
  onReset?: () => void;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col">
        <span className="text-text-primary font-mono text-xs">{label}</span>
        {ghost && <span className="text-text-muted text-overline">{ghost}</span>}
        {hint && (
          <span className="text-text-muted text-overline">ⓘ {hint}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {children}
        {onReset && (
          <button
            type="button"
            onClick={onReset}
            className="text-accent-text text-overline hover:underline"
          >
            reset
          </button>
        )}
      </div>
    </div>
  );
}
