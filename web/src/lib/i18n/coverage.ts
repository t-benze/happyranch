/**
 * @/lib/i18n/coverage — the checked mounted-route/namespace coverage manifest
 * (THR-118 W1).
 *
 * W1 ships the translation *foundation*, not a translation campaign. Every
 * mounted product surface is therefore recorded as `english-only`, and
 * redirect-only/catch-all tokens are `not-applicable`. The marker is explicit
 * machine-readable data: fallback English is never treated as coverage, and
 * the accompanying test fails when a newly mounted route token is not
 * classified here.
 *
 * `routeTokens` are the literal `path="..."` values declared in
 * `src/routes.tsx`, `src/prototypes/index.tsx` and the settings sub-route tree;
 * an `<Route index>` contributes the literal token `index`. `surfaces`
 * documents reachable shared dialogs/overlays that belong to the namespace
 * (they are inspected in the W2-W4 migration, not discovered by the
 * route-token scan). Dialog/overlay names are the ACTUAL mounted component
 * names, and `coverage.test.ts` anchors them to the real consumer sources —
 * an invented name fails the test.
 *
 * ### Colliding tokens (qualified identities)
 *
 * React Router reuses the same literal tokens for different behaviours:
 *  - `*` is the app-wide `NotFound` (copy: "Not found. Go home") in
 *    `routes.tsx`, but the settings-internal redirect in `SettingsPage.tsx`.
 *  - `index` is the root `RootRedirect` (copy: "Loading…") in `routes.tsx`,
 *    but a copy-free redirect inside `SettingsPage.tsx`.
 *
 * Where one token needs two classifications the manifest carries a qualified
 * `<scope>:<token>` identity (see `qualifiedRouteTokens`). Bare
 * `classifyRouteToken('*')` / `classifyRouteToken('index')` intentionally
 * resolve to the copy-bearing classification, because those are the surfaces a
 * fallback render can actually reach; the copy-free variants are asserted
 * through their qualified identities.
 */
import type { Locale } from './locale';
import { translate } from './catalog';

export type CoverageStatus = 'translated' | 'english-only' | 'not-applicable';

export interface NamespaceCoverage {
  namespace: string;
  routeTokens: readonly string[];
  status: CoverageStatus;
  surfaces: readonly string[];
  /**
   * `<scope>:<token>` identities for a token whose classification differs from
   * the bare token (see the file header). Scopes are the route module or the
   * mounted page name that declares the token.
   */
  qualifiedRouteTokens?: readonly string[];
}

export const COVERAGE_MANIFEST: readonly NamespaceCoverage[] = [
  {
    namespace: 'root-shell',
    routeTokens: ['index'],
    status: 'english-only',
    surfaces: ['RootRedirect (AppShell loading copy: "Loading…")'],
  },
  {
    namespace: 'not-found',
    routeTokens: ['*'],
    status: 'english-only',
    qualifiedRouteTokens: ['routes.tsx:*'],
    surfaces: ['NotFound ("Not found. Go home")'],
  },
  {
    namespace: 'redirects',
    routeTokens: ['/orgs/:slug', 'spend', 'schedule'],
    status: 'not-applicable',
    surfaces: ['OrgLayout (org-context wrapper, no copy)', 'NavigateToHome', 'SpendRedirect', 'ScheduleRedirect'],
  },
  {
    namespace: 'onboarding',
    routeTokens: ['onboarding'],
    status: 'english-only',
    surfaces: ['OnboardingPage', 'ConnectRuntimeStep'],
  },
  {
    namespace: 'dashboard',
    routeTokens: ['dashboard'],
    status: 'english-only',
    surfaces: ['DashboardPage'],
  },
  {
    namespace: 'threads',
    routeTokens: ['threads', 'threads/:thread_id'],
    status: 'english-only',
    surfaces: [
      'ThreadsPage',
      'NewThreadDialog',
      'InviteDialog',
      'ArchiveDialog',
      'RemoveParticipantDialog',
    ],
  },
  {
    namespace: 'tasks',
    routeTokens: ['tasks', 'tasks/:task_id'],
    status: 'english-only',
    surfaces: [
      'TasksPage',
      'TaskDetailPage',
      'CancelTaskDialog',
      'RevisitTaskDialog',
      'ResolveEscalationDialog',
    ],
  },
  {
    namespace: 'todos',
    routeTokens: ['todos', 'todos/:scheduleId'],
    status: 'english-only',
    surfaces: ['TodosPage', 'TodoDetailPage', 'ConfirmDialog', 'EditDialog'],
  },
  {
    namespace: 'kb',
    routeTokens: ['kb', 'kb/:entrySlug/*'],
    status: 'english-only',
    surfaces: ['KbPage', 'ComposeKbEntryDialog'],
  },
  {
    namespace: 'audit',
    routeTokens: ['audit'],
    status: 'english-only',
    surfaces: ['AuditPage', 'audit narrative'],
  },
  {
    namespace: 'skills',
    routeTokens: [
      'skills',
      'skills/validation',
      'skills/custom',
      'skills/custom/new',
      'skills/custom/:skillId',
      'skills/:skillId',
    ],
    status: 'english-only',
    surfaces: [
      'SkillsPage',
      'SkillValidationPage',
      'SkillDetailPage',
      'CustomSkillsPage',
      'CustomSkillCreatePage',
      'CustomSkillDetailPage',
    ],
  },
  {
    namespace: 'agents',
    routeTokens: ['agents', 'agents/:agent_name', 'agents/:agent_name/team-escalation-policy'],
    status: 'english-only',
    surfaces: ['AgentsPage', 'TeamEscalationPolicyPage', 'AddAgentDialog', 'NewThreadDialog'],
  },
  {
    namespace: 'jobs',
    routeTokens: ['jobs', 'jobs/:job_id'],
    status: 'english-only',
    surfaces: ['JobsPage', 'JobDetailPage', 'RunJobDialog', 'RejectJobDialog'],
  },
  {
    namespace: 'health',
    routeTokens: ['health'],
    status: 'english-only',
    surfaces: ['HealthPage'],
  },
  {
    namespace: 'usage',
    routeTokens: ['usage'],
    status: 'english-only',
    surfaces: ['UsagePage'],
  },
  {
    namespace: 'dreams',
    routeTokens: ['dreams'],
    status: 'english-only',
    surfaces: ['DreamsPage'],
  },
  {
    namespace: 'work-hours',
    routeTokens: ['work-hours', 'work-hours/:agent'],
    status: 'english-only',
    surfaces: ['WorkHoursOverviewPage', 'WorkHoursWakesView', 'WorkHoursAgentDetailPage', 'TierEditorDialog'],
  },
  {
    namespace: 'artifacts',
    routeTokens: ['artifacts'],
    status: 'english-only',
    surfaces: ['ArtifactsPage'],
  },
  {
    namespace: 'settings',
    routeTokens: ['settings/*', 'assistant', 'daemon-capacity', 'organization', 'executors'],
    status: 'english-only',
    surfaces: [
      'SettingsPage',
      'SettingsSubNav',
      'AssistantSection',
      'DaemonCapacitySection',
      'OrganizationSection',
      'ExecutorsSection',
      'ReconfigureDialog',
      'EligibilityEditorDialog',
    ],
  },
  {
    namespace: 'settings-redirects',
    routeTokens: ['system'],
    status: 'not-applicable',
    qualifiedRouteTokens: [
      'SettingsPage.tsx:index',
      'SettingsPage.tsx:system',
      'SettingsPage.tsx:agents',
      'SettingsPage.tsx:*',
    ],
    surfaces: ['settings index/system/agents/* redirects'],
  },
  {
    namespace: 'app-shell',
    routeTokens: [],
    status: 'english-only',
    surfaces: ['AppShell', 'AppBar', 'Sidebar', 'ErrorBoundary', 'AddOrgDialog'],
  },
  {
    namespace: 'system-assistant',
    routeTokens: [],
    status: 'english-only',
    surfaces: ['AssistantDockHost', 'CommandPaletteHost', 'HelpDrawerHost'],
  },
  {
    namespace: 'prototypes',
    routeTokens: ['/__prototypes', 'threads-v2', 'threads-v2/:thread_id'],
    status: 'not-applicable',
    surfaces: ['PrototypeProvider sandbox (mock data, not production copy)'],
  },
] as const;

/** Namespaces that intentionally carry no product copy (never "coverage"). */
export const NOT_APPLICABLE_NAMESPACES: readonly string[] = [
  'redirects',
  'settings-redirects',
  'prototypes',
];

function buildTokenIndex(): Map<string, NamespaceCoverage> {
  const index = new Map<string, NamespaceCoverage>();
  for (const entry of COVERAGE_MANIFEST) {
    for (const token of entry.routeTokens) {
      if (!index.has(token)) index.set(token, entry);
    }
  }
  return index;
}

function buildIdentityIndex(): Map<string, NamespaceCoverage> {
  const index = new Map<string, NamespaceCoverage>();
  for (const entry of COVERAGE_MANIFEST) {
    for (const identity of entry.qualifiedRouteTokens ?? []) {
      if (!index.has(identity)) index.set(identity, entry);
    }
  }
  return index;
}

const TOKEN_INDEX = buildTokenIndex();
const IDENTITY_INDEX = buildIdentityIndex();

/** Classify a bare route token (see the file header for collisions). */
export function classifyRouteToken(token: string): NamespaceCoverage | undefined {
  return TOKEN_INDEX.get(token);
}

/**
 * Classify a `<scope>:<token>` identity, falling back to the bare-token
 * classification (so a scoped scan of a module whose tokens do NOT collide
 * still resolves). Use this for tokens that collide across modules.
 */
export function classifyRouteIdentity(identity: string): NamespaceCoverage | undefined {
  const qualified = IDENTITY_INDEX.get(identity);
  if (qualified) return qualified;
  const bare = TOKEN_INDEX.get(identity);
  if (bare) return bare;
  const separator = identity.indexOf(':');
  if (separator === -1) return undefined;
  return TOKEN_INDEX.get(identity.slice(separator + 1));
}

/** Tokens that the manifest does not classify (the check's failure signal). */
export function unclassifiedRouteTokens(tokens: readonly string[]): string[] {
  return tokens.filter((token) => !TOKEN_INDEX.has(token));
}

/** Qualified identities that the manifest does not classify. */
export function unclassifiedRouteIdentities(identities: readonly string[]): string[] {
  return identities.filter((identity) => classifyRouteIdentity(identity) === undefined);
}

export interface CoverageSummary {
  translated: number;
  englishOnly: number;
  notApplicable: number;
  total: number;
}

export function coverageSummary(): CoverageSummary {
  const summary: CoverageSummary = { translated: 0, englishOnly: 0, notApplicable: 0, total: 0 };
  for (const entry of COVERAGE_MANIFEST) {
    summary.total += 1;
    if (entry.status === 'translated') summary.translated += 1;
    else if (entry.status === 'english-only') summary.englishOnly += 1;
    else summary.notApplicable += 1;
  }
  return summary;
}

/** The visible English marker for a surface that is not translated yet. */
export function describeCoverage(locale: Locale, status: CoverageStatus): string {
  if (status === 'english-only') return translate(locale, 'coverage.englishOnly');
  if (status === 'not-applicable') return translate(locale, 'coverage.notApplicable');
  return translate(locale, 'coverage.title');
}

/**
 * Source-scan helper: extract the literal route tokens from a route module's
 * source. `path="..."` values are returned verbatim; a bare `<Route index>`
 * contributes the literal token `index`. Used by `coverage.test.ts` against
 * `routes.tsx`, `prototypes/index.tsx` and `SettingsPage.tsx`.
 */
export function extractRouteTokens(source: string): string[] {
  const tokens: string[] = [];
  for (const match of source.matchAll(/path="([^"]*)"/g)) {
    if (!tokens.includes(match[1])) tokens.push(match[1]);
  }
  if (/<Route\s+index\b/.test(source) && !tokens.includes('index')) tokens.push('index');
  return tokens;
}
