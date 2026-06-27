/**
 * S1 — Schedule Overview (roster). One row per agent: agent · team · effective
 * mode · effective cadence · next wake · read-only On status · eligibility chip
 * · "no routine tasks" flag.
 *
 * Header has the SINGLE global feature on/off switch (working_hours.enabled)
 * with confirm-before-disable, plus entry points to edit the org default / a
 * team. Invalid-config recovery banner pinned at top when the live config
 * failed to load.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useSettings, useUpdateOrgSettings } from '@/hooks/settings';
import { useAgentsList } from '@/hooks/agents';
import { useTeamsList } from '@/hooks/teams';
import { Button } from '@/design-system/primitives/Button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/design-system/primitives/Select';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import type { AgentSummary } from '@/lib/api/types';
import {
  cadenceSummary,
  effectiveSchedule,
  isEligible,
  onStatus,
  parseRoutineTasks,
  reconcile,
} from './merge';
import {
  EligibilityChip,
  NoRoutineTasksFlag,
  OnDot,
  RecoveryBanner,
  SavedBanner,
} from './components';
import { TierEditorDialog, type Tier } from './TierEditorDialog';
import { EligibilityEditorDialog } from './EligibilityEditorDialog';
import { extractServerErrors } from './errors';
import { useAgentTeamMap } from './useAgentTeamMap';

const PENDING_TICK =
  'Saved ✓ — takes effect at the next scheduler pass (≈ within ~60s).';

export function OverviewPage(): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const settingsQuery = useSettings();
  const agentsQuery = useAgentsList();
  const teamsQuery = useTeamsList();
  const mutation = useUpdateOrgSettings();
  const queryClient = useQueryClient();

  const wh = settingsQuery.data?.org.working_hours;
  const agents: AgentSummary[] = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );
  const agentTeam = useAgentTeamMap();

  const [tier, setTier] = useState<Tier | null>(null);
  const [editEligibility, setEditEligibility] = useState(false);
  const [teamToEdit, setTeamToEdit] = useState<string>('');
  const [confirmDisable, setConfirmDisable] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const teamNames = useMemo(
    () => (teamsQuery.data?.teams ?? []).map((t) => t.name),
    [teamsQuery.data?.teams],
  );
  const allAgentNames = useMemo(() => agents.map((a) => a.name), [agents]);

  async function setEnabled(next: boolean) {
    setError(null);
    try {
      await mutation.mutateAsync({ working_hours: { enabled: next } });
      setSavedMsg(PENDING_TICK);
    } catch (err: unknown) {
      setError(extractServerErrors(err).join('; '));
    }
  }

  function onSaved() {
    setSavedMsg(PENDING_TICK);
  }

  // Loading.
  if (settingsQuery.isLoading) {
    return <div className="text-fg-muted p-6">Loading work hours…</div>;
  }

  // Config-broken-on-disk recovery: the settings query errored (e.g. the live
  // config failed to load). We can still let the founder edit toward valid by
  // showing the banner; without raw tiers we can only show the banner + the
  // global toggle is also unavailable, so guide them to the editors.
  if (settingsQuery.isError || !wh) {
    const reason =
      settingsQuery.error?.message ?? 'The work-hours config could not be read.';
    return (
      <div className="flex h-full flex-col">
        <Header slug={slug} />
        <div className="p-4">
          <RecoveryBanner reason={reason} />
          <Button
            variant="outline"
            onClick={() =>
              queryClient.invalidateQueries({ queryKey: ['settings', slug] })
            }
          >
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <Header slug={slug} />

      <div className="flex-1 overflow-y-auto p-4">
        {savedMsg && <SavedBanner message={savedMsg} />}
        {error && (
          <div
            role="alert"
            className="border-tier-red bg-feedback-danger/10 text-tier-red mb-4 rounded border p-3 text-sm"
          >
            {error}
          </div>
        )}

        {/* Feature switch + entry points */}
        <div className="border-border bg-bg-subtle mb-4 flex flex-wrap items-center gap-3 rounded-md border p-3">
          <span className="text-text-primary text-sm font-medium">
            Feature: work hours
          </span>
          <button
            type="button"
            role="switch"
            aria-checked={wh.enabled}
            aria-label="Work-hours feature on/off"
            onClick={() => {
              if (wh.enabled) setConfirmDisable(true);
              else void setEnabled(true);
            }}
            disabled={mutation.isPending}
            className={`inline-flex h-5 w-9 items-center rounded-full transition-colors ${
              wh.enabled ? 'bg-accent' : 'bg-bg-raised border-border border'
            }`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
                wh.enabled ? 'translate-x-4' : 'translate-x-0.5'
              }`}
            />
          </button>
          <span className="text-text-muted text-xs">
            {wh.enabled ? 'ON' : 'OFF'}
          </span>

          <span className="flex-1" />

          <Button variant="outline" size="sm" onClick={() => setTier({ kind: 'org' })}>
            Edit org default
          </Button>
          <Button variant="outline" size="sm" onClick={() => setEditEligibility(true)}>
            Edit eligibility
          </Button>
          <div className="flex items-center gap-1">
            <Select
              value={teamToEdit}
              onValueChange={(v) => {
                setTeamToEdit(v);
                setTier({ kind: 'team', team: v });
              }}
            >
              <SelectTrigger className="h-8 w-40">
                <SelectValue placeholder="Edit team…" />
              </SelectTrigger>
              <SelectContent>
                {teamNames.length === 0 && (
                  <SelectItem value="__none__" disabled>
                    no teams
                  </SelectItem>
                )}
                {teamNames.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Roster */}
        {agents.length === 0 ? (
          <EmptyState
            title="No agents"
            body="This org has no agents yet. Enroll agents to configure work hours."
          />
        ) : (
          <div className="border-border overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-bg-subtle text-text-muted text-xs uppercase">
                <tr>
                  <Th>Agent</Th>
                  <Th>Team</Th>
                  <Th>Mode</Th>
                  <Th>Cadence (effective)</Th>
                  <Th>On</Th>
                  <Th>Eligibility</Th>
                </tr>
              </thead>
              <tbody className="divide-border divide-y">
                {agents.map((a) => {
                  const team = agentTeam[a.name] ?? null;
                  const rec = reconcile(wh, a.name, team);
                  const eff = effectiveSchedule(rec);
                  const eligible = isEligible(wh, a.name);
                  const on = onStatus(wh, a.name);
                  const noRoutines =
                    parseRoutineTasks(a.system_prompt).length === 0;
                  return (
                    <tr key={a.name} className="hover:bg-surface-hover">
                      <td className="px-3 py-2">
                        <Link
                          to={`/orgs/${slug}/work-hours/${a.name}`}
                          className="text-accent-text font-medium hover:underline"
                        >
                          {a.name}
                        </Link>
                      </td>
                      <td className="text-text-muted px-3 py-2">{team ?? '—'}</td>
                      <td className="text-text-secondary px-3 py-2">
                        {eff.mode ?? '—'}
                      </td>
                      <td className="text-text-muted px-3 py-2 font-mono text-xs tabular-nums">
                        {cadenceSummary(eff)}
                        {on && noRoutines && (
                          <span className="ml-2">
                            <NoRoutineTasksFlag />
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <OnDot on={on} />
                      </td>
                      <td className="px-3 py-2">
                        <EligibilityChip eligible={eligible} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {tier && (
        <TierEditorDialog
          open={tier !== null}
          onOpenChange={(o) => {
            if (!o) setTier(null);
          }}
          tier={tier}
          wh={wh}
          agentTeam={agentTeam}
          allAgents={allAgentNames}
          onSaved={onSaved}
        />
      )}

      {editEligibility && (
        <EligibilityEditorDialog
          open={editEligibility}
          onOpenChange={setEditEligibility}
          wh={wh}
          allAgents={allAgentNames}
          onSaved={onSaved}
        />
      )}

      {/* Confirm-before-disable the global feature switch. */}
      <Dialog open={confirmDisable} onOpenChange={setConfirmDisable}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Disable work hours?</DialogTitle>
            <DialogDescription>
              Turning the feature off halts all scheduled wakes for every agent.
              Eligibility and tier config are preserved; nothing runs until you
              turn it back on.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmDisable(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmDisable(false);
                void setEnabled(false);
              }}
            >
              Disable
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Header({ slug }: { slug: string | undefined }): JSX.Element {
  return (
    <header className="border-border-default border-b p-4">
      <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
        Working hours · Founder-only configuration
      </p>
      <h1 className="font-display text-display text-text-primary mt-1 font-medium">
        When the org is awake.
      </h1>
      <p className="text-caption text-text-muted mt-1">
        Configure when agents wake and what they dispatch — org → team → agent,
        leaf by leaf.{' '}
        {slug && (
          <Link
            to={`/orgs/${slug}/schedule`}
            className="text-accent-text hover:underline"
          >
            View wake history
          </Link>
        )}
      </p>
    </header>
  );
}

function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 text-left font-semibold">{children}</th>;
}
