/**
 * S2 — Agent Detail. The 3-column + Effective reconciliation table (per-leaf
 * provenance), eligibility state, read-only Routine Tasks panel, and the
 * next-wakes panel. Buttons open the tier editors + eligibility editor.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useSettings } from '@/hooks/settings';
import { useNextWakes } from '@/hooks/settings';
import { useAgentsList } from '@/hooks/agents';
import { Button } from '@/design-system/primitives/Button';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import type { AgentSummary } from '@/lib/api/types';
import {
  isEligible,
  onStatus,
  parseRoutineTasks,
  reconcile,
  renderLeaf,
} from './merge';
import {
  EligibilityChip,
  OnDot,
  ProvenanceBadge,
  RecoveryBanner,
  SavedBanner,
} from './components';
import { TierEditorDialog, type Tier } from './TierEditorDialog';
import { EligibilityEditorDialog } from './EligibilityEditorDialog';
import { useAgentTeamMap } from './useAgentTeamMap';

const PENDING_TICK =
  'Saved ✓ — takes effect at the next scheduler pass (≈ within ~60s).';

export function AgentDetailPage(): JSX.Element {
  const { slug, agent } = useParams<{ slug: string; agent: string }>();
  const settingsQuery = useSettings();
  const agentsQuery = useAgentsList();
  const nextWakesQuery = useNextWakes(agent, 5);
  const agentTeam = useAgentTeamMap();

  const wh = settingsQuery.data?.org.working_hours;
  const agents: AgentSummary[] = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );
  const agentSummary = agents.find((a) => a.name === agent);
  const team = agent ? (agentTeam[agent] ?? null) : null;

  const [tier, setTier] = useState<Tier | null>(null);
  const [editEligibility, setEditEligibility] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const allAgentNames = useMemo(() => agents.map((a) => a.name), [agents]);

  if (settingsQuery.isLoading) {
    return <div className="text-fg-muted p-6">Loading work hours…</div>;
  }

  if (settingsQuery.isError || !wh) {
    return (
      <div className="p-4">
        <RecoveryBanner
          reason={
            settingsQuery.error?.message ??
            'The work-hours config could not be read.'
          }
        />
        <Link to={`/orgs/${slug}/work-hours`} className="text-accent-text text-sm hover:underline">
          ← Back to overview
        </Link>
      </div>
    );
  }

  if (!agent) {
    return <EmptyState title="No agent" body="No agent specified." />;
  }

  const rec = reconcile(wh, agent, team);
  const eligible = isEligible(wh, agent);
  const on = onStatus(wh, agent);
  const routineTasks = parseRoutineTasks(agentSummary?.system_prompt);
  const hasSystemPrompt = agentSummary?.system_prompt !== undefined;

  function onSaved() {
    setSavedMsg(PENDING_TICK);
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="border-border-default border-b p-4">
        <Link
          to={`/orgs/${slug}/work-hours`}
          className="text-text-muted text-xs hover:underline"
        >
          ← Work hours
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="font-display text-h2 text-text-primary">{agent}</h1>
          <span className="text-text-muted text-sm">{team ?? 'no team'}</span>
          <EligibilityChip eligible={eligible} />
          <OnDot on={on} />
        </div>
        {!eligible && (
          <p className="text-text-muted mt-1 text-xs">
            Excluded by the eligibility selector — this schedule is configured
            but inert.
          </p>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        {/* a-workhours wh-wrap: 1120 centered cap (THR-099 Slice 8). */}
        <div className="max-w-content-wide mx-auto p-4">
        {savedMsg && <SavedBanner message={savedMsg} />}

        {/* Reconciliation table */}
        <section className="mb-6">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-text-primary text-sm font-semibold">
              Effective schedule — provenance
            </h2>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setTier({ kind: 'org' })}>
                Edit org default
              </Button>
              {team && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setTier({ kind: 'team', team })}
                >
                  Edit team: {team}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => setTier({ kind: 'agent', agent })}
              >
                Edit this agent
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditEligibility(true)}>
                Eligibility
              </Button>
            </div>
          </div>

          <div className="border-border overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-bg-subtle text-text-muted text-xs uppercase">
                <tr>
                  <Th>Leaf</Th>
                  <Th>Org default</Th>
                  <Th>{team ? `Team: ${team}` : 'Team'}</Th>
                  <Th>This agent</Th>
                  <Th>Effective</Th>
                </tr>
              </thead>
              <tbody className="divide-border divide-y">
                {rec.rows.map((row) => (
                  <tr key={row.leaf} className="hover:bg-surface-hover">
                    <td className="text-text-primary px-3 py-1.5 font-mono text-xs">
                      {row.label}
                    </td>
                    <Cell winning={row.cell.source === 'org'}>
                      {renderLeaf(row.cell.org)}
                    </Cell>
                    <Cell winning={row.cell.source === 'team'}>
                      {renderLeaf(row.cell.team)}
                    </Cell>
                    <Cell winning={row.cell.source === 'agent'}>
                      {renderLeaf(row.cell.agent)}
                    </Cell>
                    <td className="px-3 py-1.5">
                      <span className="text-text-primary mr-2 font-mono text-xs tabular-nums">
                        ▶ {renderLeaf(row.cell.effective)}
                      </span>
                      <ProvenanceBadge source={row.cell.source} teamName={team} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Next wakes */}
        <section className="mb-6">
          <h2 className="text-text-primary mb-2 text-sm font-semibold">Next wakes</h2>
          <div className="border-border bg-bg-subtle rounded-md border p-3 text-sm">
            {nextWakesQuery.isLoading && (
              <span className="text-text-muted">Computing next wakes…</span>
            )}
            {!nextWakesQuery.isLoading && nextWakesQuery.data && (
              <NextWakes data={nextWakesQuery.data} routineTasks={routineTasks} />
            )}
            {!nextWakesQuery.isLoading && !nextWakesQuery.data && (
              <span className="text-text-muted">No preview available.</span>
            )}
          </div>
        </section>

        {/* Routine Tasks (read-only) */}
        <section>
          <h2 className="text-text-primary mb-2 text-sm font-semibold">
            Routine Tasks{' '}
            <span className="text-text-muted text-xs font-normal">
              (read-only · editing is Phase 2)
            </span>
          </h2>
          <div className="border-border bg-bg-subtle rounded-md border p-3 text-sm">
            <p className="text-text-muted mb-2 text-xs">
              ⓘ Each bullet = one root task self-dispatched per wake. No bullets →
              wake does nothing.
            </p>
            {!hasSystemPrompt ? (
              <p className="text-text-muted">
                System prompt not available — view the agent&rsquo;s{' '}
                <code className="text-text-secondary">## Routine Tasks</code>{' '}
                markdown.
              </p>
            ) : routineTasks.length === 0 ? (
              <p className="text-feedback-danger">
                This agent&rsquo;s wakes will dispatch nothing — add at least one
                routine task (edit the agent&rsquo;s{' '}
                <code>## Routine Tasks</code> markdown; in-UI editing is Phase 2).
              </p>
            ) : (
              <ul className="text-text-secondary list-disc pl-5">
                {routineTasks.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            )}
            {hasSystemPrompt && routineTasks.length > 0 && (
              <p className="text-text-muted mt-2 text-xs">
                To change these in MVP, edit the agent&rsquo;s{' '}
                <code>## Routine Tasks</code> markdown directly.
              </p>
            )}
          </div>
        </section>
        </div>
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
    </div>
  );
}

function NextWakes({
  data,
  routineTasks,
}: {
  data: import('@/lib/api/types').NextWakesResponse;
  routineTasks: string[];
}): JSX.Element {
  if (!data.enabled) {
    return (
      <span className="text-text-muted">
        Work-hours feature is OFF — no wakes scheduled.
      </span>
    );
  }
  if (data.error) {
    return (
      <span className="text-feedback-danger">
        Incomplete schedule: {data.error}
      </span>
    );
  }
  if (data.next_wakes.length === 0) {
    return <span className="text-text-muted">No upcoming wakes.</span>;
  }
  return (
    <div>
      <ol className="text-text-secondary font-mono text-xs tabular-nums">
        {data.next_wakes.map((iso) => (
          <li key={iso}>
            {new Date(iso).toLocaleString(undefined, {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
            {data.timezone ? ` (${data.timezone})` : ''}
          </li>
        ))}
      </ol>
      <p className="text-text-muted mt-2 text-xs">
        On each wake it dispatches:{' '}
        {routineTasks.length > 0 ? routineTasks.join('; ') : '(nothing — no routine tasks)'}
      </p>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 text-left font-semibold">{children}</th>;
}

function Cell({
  winning,
  children,
}: {
  winning: boolean;
  children: React.ReactNode;
}): JSX.Element {
  return (
    <td
      className={`px-3 py-1.5 font-mono text-xs tabular-nums ${
        winning ? 'bg-accent-soft text-accent-text font-semibold' : 'text-text-muted'
      }`}
    >
      {children}
    </td>
  );
}
