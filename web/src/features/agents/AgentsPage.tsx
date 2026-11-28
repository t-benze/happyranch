import { useIdentityPresentation } from '@/hooks/identities';
import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * AgentsPage — two-pane Pasture layout.
 *
 * LEFT PANE: agent roster list at w-rail (244px) with Pasture card styling,
 * role-colored avatar-initial chip, a `role`-derived meta line, role-colored
 * led dot, active-state left-marker accent bar.
 *
 * RIGHT PANE: AgentDetailPane — editable executor segmented control,
 * repo/tool chips as rounded-full tags, accountability metrics with
 * display font, real saves. Calm empty-state when no agent selected.
 *
 * NO autonomy toggle (founder ruling). NO permission-model changes.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { CircleCheck, Plus, Users } from 'lucide-react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { NewThreadDialog } from '@/shared/threads/NewThreadDialog';
import { useThreadRoutes } from '@/hooks/threads';
import { List, Row, RowAction } from '@/design-system/patterns/List';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { TopbarActions } from '@/design-system/layouts/AppShell/TopbarSlots';
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from '@/design-system/primitives/Tabs';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList, useAgentsRoutes } from '@/hooks/agents';
import { useDensity } from '@/hooks/density';
import { PendingEnrollmentsTab } from './PendingEnrollmentsTab';
import { AgentDetailPane } from './AgentDetailPane';
import type { AgentSummary } from '@/lib/api/types';
import { AddAgentDialog, type CreatedInfo } from './AddAgentDialog';
import { useTeamsList } from '@/hooks/teams';
import { useOwnerRecords } from '@/shared/owners';
import { Avatar } from '@/design-system/primitives/Avatar';
import { agentInitials } from './agentInitials';
import { RetiredAgentDetail } from './retired/RetiredAgentDetail';
import { RetiredRoster } from './retired/RetiredRoster';
import { useTranslation } from '@/hooks/i18n';

/**
 * AGENTS-02: the roster meta line is role-only — `status` is NOT a field on
 * the AgentSummary roster payload, so the absent half is omitted, never
 * fabricated. The daemon role value renders verbatim; only a missing role
 * falls back to the localized "No role" copy.
 */

export function AgentsPage(): JSX.Element {
  const { agent_name: openAgentName, slug } = useParams<{ agent_name?: string; slug?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { t, render } = useTranslation();
  const routes = useAgentsRoutes();
  const agentsQuery = useAgentsList();
  const identityPresentation = useIdentityPresentation();
  const { density } = useDensity();
  const rowPad = density === 'compact' ? 'py-1.5' : 'py-2.5';
  // THR-402: Create team and Create agent open the ONE shared form in its two modes.
  const [addOpen, setAddOpen] = useState(false);
  const [addMode, setAddMode] = useState<'team' | 'agent'>('agent');
  const [addPreselect, setAddPreselect] = useState('');
  const openCreate = (mode: 'team' | 'agent', preselect = '') => {
    setAddMode(mode);
    setAddPreselect(preselect);
    setAddOpen(true);
  };
  // THR-402 v1.1: a confirmed create closes the surface onto the returned
  // detail, which shows this completion panel (Team created → Create agent in
  // that team). A failed roster readback keeps the creation confirmed and
  // offers Refresh, a read that never creates again.
  const teamsQuery = useTeamsList();
  const [completion, setCompletion] = useState<CreatedInfo | null>(null);
  const completionHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (completion) requestAnimationFrame(() => completionHeading.current?.focus());
  }, [completion]);
  useEffect(() => setCompletion(null), [slug]);
  // Held-action chip: presentation of this org's existing `agents.create`
  // owner record while its sent create is pending or unknown and the surface
  // is closed. Open reopens the same result; nothing is sent.
  const ownerRecords = useOwnerRecords();
  const held = addOpen
    ? undefined
    : ownerRecords.find(
      (record) => record.page === 'agents.create' && record.org === (slug ?? null) && (record.status === 'pending' || record.status === 'unknown'),
    );

  // Start Thread dialog state
  const threadRoutes = useThreadRoutes();
  const [showStartThread, setShowStartThread] = useState(false);
  const [startThreadPrefill, setStartThreadPrefill] = useState<
    { recipients: string[] } | undefined
  >(undefined);

  // A selected agent pins the Active tab — Pending under a detail view
  // doesn't make sense. The URL's `view=pending` is otherwise the source
  // of truth so refresh + back/forward both round-trip.
  const tab =
    openAgentName
      ? 'active'
      : searchParams.get('view') === 'pending'
        ? 'pending'
        : searchParams.get('view') === 'terminated' ? 'terminated' : 'active';

  const onTabChange = (next: string) => {
    if (next === 'terminated') navigate(`${routes.inbox()}?view=terminated`);
    else if (next === 'pending') navigate(routes.pending());
    else navigate(routes.inbox());
  };

  // Memoized so the auto-select effect's dependency array is stable across
  // renders (the raw `?? []` fallback would be a fresh array each render).
  const agents = useMemo(
    () => agentsQuery.data?.agents ?? [],
    [agentsQuery.data?.agents],
  );
  const selectedAgent = typeof openAgentName === 'string' ? openAgentName : null;
  // THR-296 S4: a terminated row selects a retained retired source by its
  // permanent ID; the detail reads the S1 retired-source GET, not the roster.
  const selectedSource = tab === 'terminated' ? searchParams.get('source') : null;

  // AGENTS-01: on first settle of the active roster surface, auto-select the
  // first agent so its detail pane renders by default (matching the
  // Direction-A `a-agents` reference). A ref latches this to fire once — so
  // closing the detail pane (→ inbox) still returns to the calm empty state
  // instead of immediately re-selecting. Deep-links, the empty roster, and
  // the pending view are all left untouched. THR-402: while Create team /
  // Create agent is open the settle still consumes the latch but issues no
  // navigation — a dirty draft's owner gate would block it and its prompt
  // would intercept Create — and closing creation later does not replay it.
  const didAutoSelect = useRef(false);
  const selectedOrgSlug = useRef<string | undefined>(slug);
  useEffect(() => {
    if (selectedOrgSlug.current !== slug) {
      selectedOrgSlug.current = slug;
      didAutoSelect.current = false;
    }
    if (didAutoSelect.current) return;
    if (agentsQuery.isLoading || agentsQuery.isError) return;
    if (['pending', 'terminated'].includes(searchParams.get('view') ?? '')) return;
    didAutoSelect.current = true;
    if (!addOpen && !selectedAgent && agents.length > 0) {
      navigate(routes.detail(agents[0].name), { replace: true });
    }
  }, [
    agentsQuery.isLoading,
    agentsQuery.isError,
    searchParams,
    selectedAgent,
    agents,
    navigate,
    routes,
    slug,
    addOpen,
  ]);

  const handleStartThread = (agent: AgentSummary) => {
    setStartThreadPrefill({ recipients: [agent.name] });
    setShowStartThread(true);
  };

  return (
    // THR-376 r2 A13: this outer column is the page's ONE scroll container
    // (AppShell <main> is overflow-hidden). The roster rail and the detail
    // pane flow inside it instead of scrolling on their own. THR-387 S6-W2b:
    // below md too, so a short viewport (380x280) scrolls to the detail pane.
    <div className="bg-surface-canvas flex h-full flex-col overflow-y-auto">
      {/* --- Top bar --- */}
      <header className="border-border-subtle shrink-0 border-b p-4">
        <PageTitle
          title={t('agents.page.title')}
          lede={t('agents.page.meta')}
        />
        {/* AGENTS-03 / THR-376 r2 A1b: the creation actions live in the AppBar.
            THR-402 A1: Create team and Create agent are explicit actions. */}
        <TopbarActions>
          {held && (
            <Button
              size="sm"
              variant="outline"
              data-testid="create-held-chip"
              className="border-attention bg-attention-soft text-attention-text rounded-full"
              onClick={() => setAddOpen(true)}
            >
              <span className="max-w-64 truncate">
                {t(held.status === 'pending' ? 'agents.create.held.creating' : 'agents.create.held.unknown', { name: held.subject ?? '' })}
              </span>
              <span className="bg-surface-canvas text-text-primary rounded-full px-2 py-0.5 text-xs">{t('agents.create.held.open')}</span>
            </Button>
          )}
          <Button size="sm" variant="secondary" onClick={() => openCreate('team')}>
            <Users aria-hidden="true" />
            {t('agents.page.createTeam')}
          </Button>
          <Button size="sm" onClick={() => openCreate('agent')}>
            <Plus aria-hidden="true" />
            {t('agents.page.createAgent')}
          </Button>
        </TopbarActions>
        {/* THR-376 r2 Ag4: the roster switch uses the SegmentedControl look
            (`Tabs variant="segmented"` reads the same class constants) while
            keeping Radix Tabs semantics and keyboard behaviour. */}
        <Tabs value={tab} onValueChange={onTabChange} className="mt-3">
          <TabsList variant="segmented">
            <TabsTrigger variant="segmented" value="active">{t('agents.tab.active')}</TabsTrigger>
            <TabsTrigger variant="segmented" value="pending">{t('agents.tab.pending')}</TabsTrigger>
            <TabsTrigger variant="segmented" value="terminated">{t('identity.terminated')}</TabsTrigger>
          </TabsList>
        </Tabs>
      </header>

      {/* --- Two-pane body --- */}
      {/* THR-099 a-agents cap: the mockup .ag-grid caps the two-pane body
          (roster list + detail pane) at 1180 centered with height:100% and
          INTERNALLY-scrolling panes. Surgical mx-auto max-w-content wrapper —
          NOT ContentWrap, whose scroll-and-pad box would fight the full-height
          two-pane (plan §1b INNER-1180, same recipe as thread-detail Slice 4).
          The single 1180 cap covers BOTH list and detail (detail is a pane in
          the grid). The topbar stays full-bleed, matching the mockup .topbar;
          the 720px inner cap lives inside AgentDetailPane, not here. THR-376 r2
          A13: from md up the panes no longer scroll on their own; the outer
          page column above is the single scroll owner. */}
      {/* W4c: below md the roster stacks above the detail (height-capped, scrolls)
          instead of a fixed 244px rail squeezing the detail to ~90px at 390.
          THR-387 S6-W2b: below md the detail keeps its content height (no clip
          to the leftover height) and the page column above scrolls to it. */}
      <div className="max-w-content mx-auto flex w-full flex-1 flex-col md:flex-row">
        {/* LEFT: Roster list — Pasture w-rail (244px) from md up */}
        <aside className="border-border-subtle bg-surface-sunken max-h-48 w-full shrink-0 overflow-y-auto border-b md:max-h-none md:w-rail md:overflow-visible md:border-r md:border-b-0">
          <Tabs value={tab}>
            <TabsContent value="active" className="mt-0">
              {agentsQuery.isLoading ? (
                <div role="status" aria-label={t('agents.common.loading')} className="animate-pulse space-y-2 p-3">
                  {[1, 2, 3, 4, 5].map((i) => (
                    <div
                      key={i}
                      className="bg-surface-raised h-10 w-full rounded-lg"
                    />
                  ))}
                </div>
              ) : agentsQuery.isError ? (
                <div className="space-y-2 p-4" role="alert">
                  <p className="text-text-muted text-sm">{t('agents.page.loadError')}</p>
                  <Button variant="outline" size="sm" onClick={() => agentsQuery.refetch()}>
                    {t('common.retry')}
                  </Button>
                </div>
              ) : agents.length === 0 ? (
                <div className="p-4">
                  <EmptyState title={t('agents.empty.title')} body={t('agents.empty.body')} />
                </div>
              ) : (
                <div className="p-2">
                  <List>
                    {agents.map((a) => {
                      const active = selectedAgent === a.name;
                      return (
                        <Row key={a.name} padding="none">
                          <RowAction
                            onClick={() => navigate(routes.detail(a.name))}
                            className={`relative overflow-hidden px-3 ${rowPad} ${
                              active ? 'bg-accent-soft' : ''
                            }`}
                          >
                            {active && (
                              <span
                                aria-hidden="true"
                                className="bg-accent-default absolute top-1 bottom-1 left-0 w-0.5 rounded-full"
                              />
                            )}
                            <div className="flex items-start gap-2.5">
                              {/* AGENTS-02: role-colored avatar-initial chip,
                                  mirroring the existing led-dot role logic. */}
                              <Avatar initials={agentInitials(identityPresentation.name(a.name))} tone={a.role === 'manager' ? 'manager' : 'worker'} size="lg" />
                              <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                  <span className="font-display text-text-primary truncate text-sm font-medium">
                                    <IdentityName canonicalId={a.name} />
                                  </span>
                                  {/* AGENTS-04: role dot on the row's right edge
                                      (Direction-A `a-agents`), not crowding the
                                      name. Role-colored — never an active/idle
                                      status the roster payload doesn't carry. */}
                                  <span
                                    aria-hidden="true"
                                    className={`ml-auto inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                                      a.role === 'manager'
                                        ? 'bg-agent-manager'
                                        : 'bg-agent-worker'
                                    }`}
                                  />
                                </div>
                                <div className="text-text-muted mt-0.5 text-xs">
                                  {a.role ?? t('agents.role.none')}
                                </div>
                                {a.description && (
                                  <p className="text-text-muted mt-0.5 truncate text-xs leading-relaxed">
                                    {a.description}
                                  </p>
                                )}
                              </div>
                            </div>
                          </RowAction>
                        </Row>
                      );
                    })}
                  </List>
                </div>
              )}
            </TabsContent>
            <TabsContent value="terminated" className="mt-0">
              <RetiredRoster selected={selectedSource} onSelect={(id) => navigate(`${routes.inbox()}?view=terminated&source=${encodeURIComponent(id)}`)} />
            </TabsContent>
            <TabsContent value="pending" className="mt-0 p-3">
              <PendingEnrollmentsTab />
            </TabsContent>
          </Tabs>
        </aside>

        {/* RIGHT: Detail/Edit pane */}
        <main className="bg-surface-canvas min-w-0 flex-1">
          {tab === 'terminated' && selectedSource ? (
            <RetiredAgentDetail key={selectedSource} sourceId={selectedSource} />
          ) : tab === 'terminated' ? (
            <div className="text-text-muted flex h-full items-center justify-center p-4">
              <p className="text-text-muted max-w-sm text-center text-sm">{t('knowledge.roster.select')}</p>
            </div>
          ) : selectedAgent ? (
            <>
            {completion && completion.canonicalId === selectedAgent && (
              <section
                role="status"
                aria-labelledby="create-completion-heading"
                data-testid="create-completion"
                className="border-tier-green/40 bg-tier-green-tint mx-4 mt-4 flex items-start gap-3 rounded-md border p-4 md:mx-6"
              >
                <CircleCheck aria-hidden="true" className="text-tier-green mt-0.5 h-5 w-5 shrink-0" />
                <div className="min-w-0 flex-1 text-sm break-words [&_b]:font-semibold">
                  <h2 id="create-completion-heading" ref={completionHeading} tabIndex={-1} className="text-text-primary font-semibold focus:outline-none">
                    {t(agentsQuery.isError ? 'agents.create.done.refreshTitle' : completion.mode === 'team' ? 'agents.create.done.teamTitle' : 'agents.create.done.agentTitle')}
                  </h2>
                  <p className="text-text-primary mt-1">
                    {agentsQuery.isError
                      ? t('agents.create.done.refreshBody')
                      : completion.mode === 'team'
                        ? render('agents.create.done.teamBody', { team: <b>{completion.team}</b>, name: <b>{completion.name}</b> })
                        : render('agents.create.done.agentBody', { team: <b>{completion.team}</b>, name: <b>{completion.name}</b> })}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {agentsQuery.isError && (
                      <Button size="sm" variant="outline" onClick={() => void agentsQuery.refetch()}>
                        {t('agents.create.success.refresh')}
                      </Button>
                    )}
                    {completion.mode === 'team' && (
                      <Button
                        size="sm"
                        disabled={!(teamsQuery.data?.teams ?? []).some((entry) => entry.name === completion.team)}
                        onClick={() => {
                          const team = completion.team;
                          setCompletion(null);
                          openCreate('agent', team);
                        }}
                      >
                        <Plus aria-hidden="true" />
                        {t('agents.create.success.addToTeam', { team: completion.team })}
                      </Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => setCompletion(null)}>
                      {t('agents.create.done')}
                    </Button>
                  </div>
                </div>
              </section>
            )}
            <AgentDetailPane
              agentName={selectedAgent}
              onClose={() => navigate(routes.inbox())}
              onRetired={(name) =>
                navigate(`${routes.inbox()}?view=terminated&source=${encodeURIComponent(name)}`)}
              onStartThread={
                agents.find((a) => a.name === selectedAgent)
                  ? () => handleStartThread(agents.find((a) => a.name === selectedAgent)!)
                  : undefined
              }
            />
            </>
          ) : (
            <div className="text-text-muted flex h-full items-center justify-center">
              <div className="text-center">
                <p className="font-display text-text-primary text-lg font-medium">
                  {agents.length > 0
                    ? t('agents.select.title')
                    : t('agents.noAgents.title')}
                </p>
                <p className="text-text-muted mt-2 text-sm">
                  {agents.length > 0
                    ? t('agents.select.body')
                    : t('agents.empty.body')}
                </p>
                {/* THR-402 A1: the empty roster offers both creation actions
                    here, where they stay visible at every width (the roster
                    rail is height-capped below md). */}
                {agents.length === 0 && !agentsQuery.isLoading && !agentsQuery.isError && (
                  <div className="mt-4 flex flex-wrap justify-center gap-2">
                    <Button size="sm" variant="secondary" onClick={() => openCreate('team')}>
                      {t('agents.page.createTeam')}
                    </Button>
                    <Button size="sm" onClick={() => openCreate('agent')}>
                      {t('agents.page.createAgent')}
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )}
        </main>
      </div>

      <AddAgentDialog
        open={addOpen}
        mode={addMode}
        preselectTeam={addPreselect}
        onOpenChange={setAddOpen}
        onCreated={setCompletion}
      />

      <NewThreadDialog
        open={showStartThread}
        onClose={() => setShowStartThread(false)}
        prefill={startThreadPrefill}
        onCreated={(newId) => navigate(threadRoutes.detail(newId))}
        agents={agents}
        reflection={
          startThreadPrefill?.recipients?.length === 1
            ? { recipients: startThreadPrefill.recipients }
            : undefined
        }
      />
    </div>
  );
}
