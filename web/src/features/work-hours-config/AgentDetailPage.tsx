/**
 * S2 — Agent Detail. The 3-column + Effective reconciliation table (per-leaf
 * provenance), eligibility state, read-only Routine Tasks panel, and the
 * next-wakes panel. Tier editing remains here; eligibility editing moved to
 * Settings → Organization → Operating controls.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useSettings } from '@/hooks/settings';
import { useNextWakes } from '@/hooks/settings';
import { useAgentsList } from '@/hooks/agents';
import { Button } from '@/design-system/primitives/Button';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTranslation } from '@/hooks/i18n';
import { formatDateShapeFor } from '@/lib/i18n';
import type { AgentSummary } from '@/lib/api/types';
import {
  isEligible,
  onStatus,
  parseRoutineTasks,
  reconcile,
  renderLeaf,
} from './merge';
import { SavedBanner } from '@/shared/work-hours/SavedBanner';
import {
  EligibilityChip,
  OnDot,
  ProvenanceBadge,
  RecoveryBanner,
} from './components';
import { TierEditorDialog, type Tier } from './TierEditorDialog';
import { useAgentTeamMap } from './useAgentTeamMap';
import { classifyWorkHoursError, renderWorkHoursError } from './strings';

export function AgentDetailPage(): JSX.Element {
  const { t, render } = useTranslation();
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
  // Locale-neutral flag: the banner copy is translated on every render.
  const [saved, setSaved] = useState(false);

  const allAgentNames = useMemo(() => agents.map((a) => a.name), [agents]);

  if (settingsQuery.isLoading) {
    return <div className="text-fg-muted p-6">{t('workHours.loading')}</div>;
  }

  if (settingsQuery.isError || !wh) {
    return (
      <div className="p-4">
        <RecoveryBanner
          reason={renderWorkHoursError(
            classifyWorkHoursError(settingsQuery.error, 'workHours.recovery.unreadable'),
            t,
          )}
        />
        <Link to={`/orgs/${slug}/work-hours`} className="text-accent-text text-sm hover:underline">
          {t('workHours.detail.backToOverview')}
        </Link>
      </div>
    );
  }

  if (!agent) {
    return (
      <EmptyState
        title={t('workHours.detail.noAgent.title')}
        body={t('workHours.detail.noAgent.body')}
      />
    );
  }

  const rec = reconcile(wh, agent, team);
  const eligible = isEligible(wh, agent);
  const on = onStatus(wh, agent);
  const routineTasks = parseRoutineTasks(agentSummary?.system_prompt);
  const hasSystemPrompt = agentSummary?.system_prompt !== undefined;

  function onSaved() {
    setSaved(true);
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="border-border-default border-b p-4">
        <Link
          to={`/orgs/${slug}/work-hours`}
          className="text-text-muted text-xs hover:underline"
        >
          {t('workHours.detail.backToWorkHours')}
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="font-display text-h2 text-text-primary">{agent}</h1>
          <span className="text-text-muted text-sm">{team ?? t('workHours.detail.noTeam')}</span>
          <EligibilityChip eligible={eligible} />
          <OnDot on={on} />
        </div>
        {!eligible && (
          <p className="text-text-muted mt-1 text-xs">
            {t('workHours.detail.excludedNote')}
          </p>
        )}
      </header>

      <div className="flex-1 overflow-y-auto">
        {/* a-workhours wh-wrap: 1120 centered cap (THR-099 Slice 8). */}
        <div className="max-w-content-wide mx-auto p-4">
        {saved && <SavedBanner message={t('workHours.saved')} />}

        {/* Reconciliation table */}
        <section className="mb-6">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-text-primary text-sm font-semibold">
              {t('workHours.detail.provenanceHeading')}
            </h2>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setTier({ kind: 'org' })}>
                {t('workHours.editOrgDefault')}
              </Button>
              {team && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setTier({ kind: 'team', team })}
                >
                  {t('workHours.editTeam', { team })}
                </Button>
              )}
              <Button
                size="sm"
                variant="outline"
                onClick={() => setTier({ kind: 'agent', agent })}
              >
                {t('workHours.detail.editAgent')}
              </Button>
              <Link
                to={`/orgs/${slug}/settings/organization`}
                className="text-accent-text text-xs hover:underline self-center"
              >
                {t('workHours.manageOperatingControl')}
              </Link>
            </div>
          </div>

          <div className="border-border overflow-hidden rounded-md border">
            <table className="w-full text-sm">
              <thead className="bg-bg-subtle text-text-muted text-xs uppercase">
                <tr>
                  <Th>{t('workHours.detail.col.leaf')}</Th>
                  <Th>{t('workHours.provenance.org')}</Th>
                  <Th>
                    {team
                      ? t('workHours.provenance.teamNamed', { team })
                      : t('workHours.provenance.team')}
                  </Th>
                  <Th>{t('workHours.provenance.agent')}</Th>
                  <Th>{t('workHours.detail.col.effective')}</Th>
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
          <h2 className="text-text-primary mb-2 text-sm font-semibold">
            {t('workHours.detail.nextWakes')}
          </h2>
          <div className="border-border bg-bg-subtle rounded-md border p-3 text-sm">
            {nextWakesQuery.isLoading && (
              <span className="text-text-muted">{t('workHours.detail.computing')}</span>
            )}
            {!nextWakesQuery.isLoading && nextWakesQuery.data && (
              <NextWakes data={nextWakesQuery.data} routineTasks={routineTasks} />
            )}
            {!nextWakesQuery.isLoading && !nextWakesQuery.data && (
              <span className="text-text-muted">{t('workHours.detail.noPreview')}</span>
            )}
          </div>
        </section>

        {/* Routine Tasks (read-only) */}
        <section>
          <h2 className="text-text-primary mb-2 text-sm font-semibold">
            {t('workHours.detail.routineHeading')}{' '}
            <span className="text-text-muted text-xs font-normal">
              {t('workHours.detail.routineReadOnly')}
            </span>
          </h2>
          <div className="border-border bg-bg-subtle rounded-md border p-3 text-sm">
            <p className="text-text-muted mb-2 text-xs">
              {t('workHours.detail.routineInfo')}
            </p>
            {!hasSystemPrompt ? (
              <p className="text-text-muted">
                {render('workHours.detail.noSystemPrompt', {
                  code: <code className="text-text-secondary">## Routine Tasks</code>,
                })}
              </p>
            ) : routineTasks.length === 0 ? (
              <p className="text-feedback-danger">
                {render('workHours.detail.dispatchNothingWarning', {
                  code: <code>## Routine Tasks</code>,
                })}
              </p>
            ) : (
              <ul className="text-text-secondary list-disc pl-5">
                {routineTasks.map((task, i) => (
                  <li key={i}>{task}</li>
                ))}
              </ul>
            )}
            {hasSystemPrompt && routineTasks.length > 0 && (
              <p className="text-text-muted mt-2 text-xs">
                {render('workHours.detail.changeHint', { code: <code>## Routine Tasks</code> })}
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
  const { t, locale } = useTranslation();
  if (!data.enabled) {
    return <span className="text-text-muted">{t('workHours.detail.featureOff')}</span>;
  }
  if (data.error) {
    return (
      <span className="text-feedback-danger">
        {t('workHours.detail.incomplete', { error: data.error })}
      </span>
    );
  }
  if (data.next_wakes.length === 0) {
    return <span className="text-text-muted">{t('workHours.detail.noUpcoming')}</span>;
  }
  return (
    <div>
      <ol className="text-text-secondary font-mono text-xs tabular-nums">
        {data.next_wakes.map((iso) => (
          <li key={iso}>
            {formatDateShapeFor(locale, new Date(iso), 'monthDayTime')}
            {data.timezone ? ` (${data.timezone})` : ''}
          </li>
        ))}
      </ol>
      <p className="text-text-muted mt-2 text-xs">
        {t('workHours.detail.dispatches', {
          tasks:
            routineTasks.length > 0
              ? routineTasks.join('; ')
              : t('workHours.detail.dispatchesNothing'),
        })}
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
