/**
 * S1 — Schedule Overview (roster). One row per agent: agent · team · effective
 * mode · effective cadence · next wake · read-only On status · eligibility chip
 * · "no routine tasks" flag.
 *
 * Read-only status bar shows derived enabled/disabled state with a
 * "Manage operating control" deep link to Settings → Organization.
 * Tier editing for org default / team remains here.
 *
 * Invalid-config recovery banner pinned at top when the live config
 * failed to load.
 */
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useSettings } from '@/hooks/settings';
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
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { useTranslation } from '@/hooks/i18n';
import type { AgentSummary } from '@/lib/api/types';
import {
  effectiveSchedule,
  isEligible,
  onStatus,
  parseRoutineTasks,
  reconcile,
} from './merge';
import { SavedBanner } from '@/shared/work-hours/SavedBanner';
import {
  EligibilityChip,
  NoRoutineTasksFlag,
  OnDot,
  RecoveryBanner,
  WorkHoursTabs,
} from './components';
import { TierEditorDialog, type Tier } from './TierEditorDialog';
import { useAgentTeamMap } from './useAgentTeamMap';
import { cadenceSummaryFor, classifyWorkHoursError, renderWorkHoursError } from './strings';

export function OverviewPage(): JSX.Element {
  const { t } = useTranslation();
  const { slug } = useParams<{ slug: string }>();
  const settingsQuery = useSettings();
  const agentsQuery = useAgentsList();
  const teamsQuery = useTeamsList();

  const wh = settingsQuery.data?.org.working_hours;
  const agents: AgentSummary[] = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );
  const agentTeam = useAgentTeamMap();

  const [tier, setTier] = useState<Tier | null>(null);
  const [teamToEdit, setTeamToEdit] = useState<string>('');
  // Locale-neutral flag: the banner copy is translated on every render.
  const [saved, setSaved] = useState(false);

  const teamNames = useMemo(
    () => (teamsQuery.data?.teams ?? []).map((t) => t.name),
    [teamsQuery.data?.teams],
  );
  const allAgentNames = useMemo(() => agents.map((a) => a.name), [agents]);


  function onSaved() {
    setSaved(true);
  }

  // Loading.
  if (settingsQuery.isLoading) {
    return <div className="text-fg-muted p-6">{t('workHours.loading')}</div>;
  }

  // Config-broken-on-disk recovery: the settings query errored (e.g. the live
  // config failed to load). We can still let the founder edit toward valid by
  // showing the banner; without raw tiers we can only show the banner + the
  // global toggle is also unavailable, so guide them to the editors.
  if (settingsQuery.isError || !wh) {
    const reason = renderWorkHoursError(
      classifyWorkHoursError(settingsQuery.error, 'workHours.recovery.unreadable'),
      t,
    );
    return (
      <div className="flex h-full min-w-0 flex-col">
        <Header slug={slug} />
        <WorkHoursTabs slug={slug} active="overview" />
        <div className="p-4">
          <RecoveryBanner reason={reason} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-w-0 flex-col">
      <Header slug={slug} />
      <WorkHoursTabs slug={slug} active="overview" />

      <div className="min-w-0 flex-1 overflow-y-auto">
        {/* a-workhours wh-wrap: 1120 centered cap (THR-099 Slice 8). */}
        <div className="max-w-content-wide mx-auto min-w-0 p-4">
        {saved && <SavedBanner message={t('workHours.saved')} />}

        {/* Read-only status bar + tier editing */}
        <div className="border-border bg-bg-subtle mb-4 flex flex-wrap items-center gap-3 rounded-md border p-3">
          <span className="text-text-primary text-sm font-medium">
            {t('workHours.statusBar.label')}
          </span>
          <span
            className={`inline-flex h-5 w-9 items-center rounded-full ${
              wh.enabled ? 'bg-accent' : 'bg-bg-raised border-border border'
            }`}
            aria-hidden="true"
          >
            <span
              className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow ${
                wh.enabled ? 'ml-auto mr-0.5' : 'ml-0.5'
              }`}
            />
          </span>
          <span className="text-text-muted text-xs">
            {wh.enabled ? t('workHours.statusBar.on') : t('workHours.statusBar.off')}
          </span>

          <span className="flex-1" />

          <Link
            to={`/orgs/${slug}/settings/organization`}
            className="text-accent-text text-xs hover:underline"
          >
            {t('workHours.manageOperatingControl')}
          </Link>

          <Button variant="outline" size="sm" onClick={() => setTier({ kind: 'org' })}>
            {t('workHours.editOrgDefault')}
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
                <SelectValue placeholder={t('workHours.editTeamPlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {teamNames.length === 0 && (
                  <SelectItem value="__none__" disabled>
                    {t('workHours.noTeams')}
                  </SelectItem>
                )}
                {teamNames.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}{teamsQuery.data?.teams.some((entry) => entry.name === name && entry.manager_kind === 'human')
                      ? ` · ${t('agents.team.founderManaged')}` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Roster fetches are distinct from a successful empty roster. Keep
            the tier editor mounted during background query errors/retries. */}
        {agentsQuery.isLoading || teamsQuery.isLoading ? (
          <p role="status" aria-label={t('workHours.roster.loading')} className="text-text-muted text-sm">
            {t('workHours.roster.loading')}
          </p>
        ) : agentsQuery.isError || teamsQuery.isError ? (
          <div role="alert" className="space-y-2">
            <p className="text-text-muted text-sm">{t('workHours.roster.loadError')}</p>
            <Button variant="outline" size="sm" onClick={() => {
              if (agentsQuery.isError) void agentsQuery.refetch();
              if (teamsQuery.isError) void teamsQuery.refetch();
            }}>{t('common.retry')}</Button>
          </div>
        ) : agents.length === 0 ? (
          <EmptyState
            title={t('workHours.empty.title')}
            body={t('workHours.empty.body')}
          />
        ) : (
          <>
          <p className="text-text-muted mb-2 text-xs">{t('workHours.scrollHint')}</p>
          <div
            role="region"
            aria-label={t('workHours.roster.scrollLabel')}
            tabIndex={0}
            className="border-border focus-visible:ring-accent-ring overflow-x-auto rounded-md border focus-visible:ring-2 focus-visible:outline-none"
          >
            <table className="w-full min-w-max text-sm">
              <thead className="bg-bg-subtle text-text-muted text-xs uppercase">
                <tr>
                  <Th>{t('workHours.roster.agent')}</Th>
                  <Th>{t('workHours.roster.team')}</Th>
                  <Th>{t('workHours.roster.mode')}</Th>
                  <Th>{t('workHours.roster.cadence')}</Th>
                  <Th>{t('workHours.roster.on')}</Th>
                  <Th>{t('workHours.roster.eligibility')}</Th>
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
                        {cadenceSummaryFor(eff, t)}
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
          </>
        )}
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

function Header({ slug }: { slug: string | undefined }): JSX.Element {
  const { t } = useTranslation();
  return (
    <header className="border-border-default border-b p-4">
      <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
        {t('workHours.header.eyebrow')}
      </p>
      <h1 className="font-display text-display text-text-primary mt-1 font-medium">
        {t('workHours.header.title')}
      </h1>
      <p className="text-caption text-text-muted mt-1">
        {t('workHours.header.description')}{' '}
        {slug && (
          <Link
            to={`/orgs/${slug}/work-hours?view=wakes`}
            className="text-accent-text hover:underline"
          >
            {t('workHours.header.wakeHistory')}
          </Link>
        )}
      </p>
    </header>
  );
}

function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 text-left font-semibold">{children}</th>;
}
