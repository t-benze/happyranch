/**
 * @/lib/i18n/coverage — the checked mounted-route/namespace coverage manifest
 * (THR-118 W1).
 *
 * W1 shipped the translation *foundation*, not a translation campaign. W2a
 * (THR-118) migrated the mounted shell: the root loading/not-found copy,
 * the AppShell chrome (AppBar/Sidebar/ErrorBoundary/AddOrgDialog) and the
 * shared help/palette presentation. W2b (THR-118) migrates the onboarding
 * route (`/onboarding`: OnboardingPage, ConnectRuntimeStep and the shared
 * ConnectFlow). W2c (THR-118) migrates the Settings surface (page chrome and
 * the Assistant/Organization/Executors/Daemon-Capacity sections) and adds the
 * `preferences` route (`PreferencesSection`), production-gated until W3b-2.
 * The shared
 * Work Hours-owned `EligibilityEditorDialog` mounted by Organization stayed
 * English until W4 and is migrated by W4b. W3a migrates Dashboard + Threads and W3b-1 migrates the
 * Tasks route family (`tasks`, `tasks/:task_id` and its owned dialogs); W3b-2
 * migrates the Jobs route family (`jobs`, `jobs/:job_id` and its owned
 * dialogs) and opens the Preferences language preview in production. W4a-1
 * migrates Runtime Health (`health`) and Dreams (`dreams`, incl. the dream
 * detail drawer). W4b migrates Todos (`todos`, `todos/:scheduleId` and its
 * owned dialogs), Work Hours (`work-hours`, `work-hours/:agent`, the
 * TierEditorDialog and the shared EligibilityEditorDialog) and Audit (`audit`,
 * incl. the catalog-templated narrative). W4c migrates Agents (`agents`,
 * `agents/:agent_name`, `agents/:agent_name/team-escalation-policy` and its
 * owned dialogs/panels) and Skills (every `skills*` route token and its owned
 * surfaces); user/daemon values (policy bodies, contract ids, digests, agent
 * names, skill names/slugs/bodies, versions, provenance) stay verbatim. W4d-1 migrates KB and Artifacts including gated Compose and upload/action chrome.
 * W4d-2 translates Usage presentation; the mounted Assistant dock and
 * conversation controls are also translated. W5a completes the accepted finite
 * mounted-state audit; W5b enables full browser locale resolution. The 21
 * translated/3 not-applicable entries are inventory, not rendering proof.
 * Fallback English is never treated as coverage.
 * Redirect-only/catch-all
 * tokens are `not-applicable`. The marker is explicit machine-readable data
 * and the accompanying test fails when a newly mounted route token is not
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
  /** Exact consumer -> owner identities for new source-owned overlay sites. */
  qualifiedSurfaces?: readonly string[];
  /** Historical names remain readable, but cannot classify a different owner. */
  surfaceOwners?: Readonly<Record<string, string>>;
}

export const COVERAGE_MANIFEST: readonly NamespaceCoverage[] = [
  {
    namespace: 'root-shell',
    routeTokens: ['index'],
    status: 'translated',
    surfaces: ['RootRedirect (AppShell loading copy)'],
  },
  {
    namespace: 'not-found',
    routeTokens: ['*'],
    status: 'translated',
    qualifiedRouteTokens: ['routes.tsx:*'],
    surfaces: ['NotFound (message + Go home link)'],
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
    status: 'translated',
    // Shared <ConnectFlow> is translated by W2b and is mounted here; its
    // Settings ▸ Executors mount does NOT make the `settings` namespace
    // translated (the surrounding Settings chrome/sections remain English-only).
    surfaces: ['OnboardingPage', 'ConnectRuntimeStep', 'ConnectFlow (shared)'],
  },
  {
    namespace: 'dashboard',
    routeTokens: ['dashboard'],
    // W3a: DashboardPage and its mounted cards/narratives/states.
    status: 'translated',
    surfaces: ['DashboardPage'],
  },
  {
    namespace: 'threads',
    surfaceOwners: {
      NewThreadDialog: 'src/shared/threads/NewThreadDialog.tsx',
      InviteDialog: 'src/features/threads/InviteDialog.tsx',
      ArchiveDialog: 'src/features/threads/ArchiveDialog.tsx',
      RemoveParticipantDialog: 'src/features/threads/RemoveParticipantDialog.tsx',
    },
    routeTokens: ['threads', 'threads/:thread_id'],
    // W3a: list/detail panes, composer, strips and the directly owned dialogs
    // (incl. the shared NewThreadDialog it mounts).
    status: 'translated',
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
    surfaceOwners: {
      CancelTaskDialog: 'src/features/tasks/CancelTaskDialog.tsx',
      RevisitTaskDialog: 'src/features/tasks/RevisitTaskDialog.tsx',
      ResolveEscalationDialog: 'src/features/tasks/ResolveEscalationDialog.tsx',
    },
    routeTokens: ['tasks', 'tasks/:task_id'],
    // W3b-1: list/detail panes, filters, status/fan-out presentation, states
    // and the directly owned dialogs. Jobs stays english-only until W3b-2.
    status: 'translated',
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
    surfaceOwners: {
      ConfirmDialog: 'src/features/todos/components/ConfirmDialog.tsx',
      EditDialog: 'src/features/todos/components/EditDialog.tsx',
    },
    routeTokens: ['todos', 'todos/:scheduleId'],
    status: 'translated',
    surfaces: ['TodosPage', 'TodoDetailPage', 'ConfirmDialog', 'EditDialog'],
  },
  {
    namespace: 'kb',
    surfaceOwners: { ComposeKbEntryDialog: 'src/features/kb/ComposeKbEntryDialog.tsx' },
    routeTokens: ['kb', 'kb/:entrySlug/*'],
    status: 'translated',
    surfaces: ['KbPage', 'ComposeKbEntryDialog'],
  },
  {
    namespace: 'audit',
    routeTokens: ['audit'],
    status: 'translated',
    surfaces: ['AuditPage', 'audit narrative'],
  },
  {
    namespace: 'skills',
    surfaceOwners: { CustomSkillDetailPage: 'src/features/skills/CustomSkillDetailPage.tsx' },
    routeTokens: [
      'skills',
      'skills/validation',
      'skills/custom',
      'skills/custom/new',
      'skills/custom/:skillId',
      'skills/:skillId',
    ],
    status: 'translated',
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
    surfaceOwners: {
      AddAgentDialog: 'src/features/agents/AddAgentDialog.tsx',
      NewThreadDialog: 'src/shared/threads/NewThreadDialog.tsx',
    },
    routeTokens: ['agents', 'agents/:agent_name', 'agents/:agent_name/team-escalation-policy'],
    status: 'translated',
    surfaces: ['AgentsPage', 'TeamEscalationPolicyPage', 'AddAgentDialog', 'NewThreadDialog'],
  },
  {
    namespace: 'jobs',
    surfaceOwners: {
      RunJobDialog: 'src/features/jobs/RunJobDialog.tsx',
      RejectJobDialog: 'src/features/jobs/RejectJobDialog.tsx',
    },
    routeTokens: ['jobs', 'jobs/:job_id'],
    status: 'translated',
    surfaces: ['JobsPage', 'JobDetailPage', 'RunJobDialog', 'RejectJobDialog'],
  },
  {
    namespace: 'health',
    routeTokens: ['health'],
    status: 'translated',
    surfaces: ['HealthPage'],
  },
  {
    namespace: 'usage',
    routeTokens: ['usage'],
    status: 'translated',
    surfaces: ['UsagePage'],
  },
  {
    namespace: 'dreams',
    routeTokens: ['dreams'],
    status: 'translated',
    surfaces: ['DreamsPage'],
  },
  {
    namespace: 'work-hours',
    surfaceOwners: { TierEditorDialog: 'src/features/work-hours-config/TierEditorDialog.tsx' },
    routeTokens: ['work-hours', 'work-hours/:agent'],
    status: 'translated',
    surfaces: ['WorkHoursOverviewPage', 'WorkHoursWakesView', 'WorkHoursAgentDetailPage', 'TierEditorDialog'],
  },
  {
    namespace: 'artifacts',
    routeTokens: ['artifacts'],
    status: 'translated',
    surfaces: ['ArtifactsPage'],
  },
  {
    namespace: 'settings',
    surfaceOwners: {
      EligibilityEditorDialog: 'src/shared/work-hours/EligibilityEditorDialog.tsx',
    },
    // Explicit panels own loading/error chrome; compatibility routes below
    // remain copy-free redirects outside the settings API gate.
    // `preferences` (W2c) is mounted in ordinary production builds since W3b-2.
    routeTokens: [
      'settings/*',
      'daemon-capacity',
      'organization',
      'executors',
      'preferences',
    ],
    status: 'translated',
    surfaces: [
      'SettingsPage',
      'SettingsSubNav',
      'DaemonCapacitySection',
      'OrganizationSection',
      'ExecutorsSection',
      'PreferencesSection',
      'EligibilityEditorDialog',
    ],
  },
  {
    namespace: 'settings-redirects',
    routeTokens: ['system', 'assistant'],
    status: 'not-applicable',
    qualifiedRouteTokens: [
      'SettingsPage.tsx:index',
      'SettingsPage.tsx:assistant',
      'SettingsPage.tsx:system',
      'SettingsPage.tsx:agents',
      'SettingsPage.tsx:*',
    ],
    surfaces: ['settings index/assistant/system/agents/* redirects'],
  },
  {
    namespace: 'app-shell',
    surfaceOwners: { AddOrgDialog: 'src/features/orgs/AddOrgDialog.tsx' },
    routeTokens: [],
    status: 'translated',
    surfaces: ['AppShell', 'AppBar', 'Sidebar', 'ErrorBoundary', 'AddOrgDialog'],
  },
  {
    namespace: 'help-and-palette',
    routeTokens: [],
    status: 'translated',
    surfaces: ['HelpDrawerHost', 'CommandPaletteHost'],
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

/** Static-source release boundary. Keep Node/fs in scripts, outside this barrel. */
export interface SourceInventory {
  modules: string[];
  mountedSymbols: string[];
  routes: { path: string; symbol: string; token: string; parents?: string[]; copyFree?: boolean }[];
  dialogs: { path: string; symbol: string; consumerPath: string; consumerSymbol: string; site: string }[];
}

/** New modules cannot borrow the historical bare index/wildcard classification. */
export function classifySourceRoute(path: string, token: string, manifest: readonly NamespaceCoverage[] = COVERAGE_MANIFEST): NamespaceCoverage | undefined {
  const exact = manifest.find(entry => entry.qualifiedRouteTokens?.includes(`${path}:${token}`));
  if (exact) return exact;
  const legacyScope = path === 'src/routes.tsx' ? 'routes.tsx' : path === 'src/features/settings/SettingsPage.tsx' ? 'SettingsPage.tsx' : undefined;
  if (!legacyScope) return undefined;
  return manifest.find(entry => entry.qualifiedRouteTokens?.includes(`${legacyScope}:${token}`))
    ?? manifest.find(entry => entry.routeTokens.includes(token));
}

function consumerNamespace(path: string, symbol: string): string | undefined {
  const domain = /^src\/features\/([^/]+)\//.exec(path)?.[1];
  if (domain) return domain === 'work-hours-config' ? 'work-hours' : domain;
  if (path.startsWith('src/design-system/layouts/AppShell/') || path === 'src/routes.tsx' && symbol === 'AppShell') return 'app-shell';
  if (path.startsWith('src/host/')) return 'help-and-palette';
  return undefined;
}

export function sourceSurfaceIdentity(site: SourceInventory['dialogs'][number]): string {
  return `${site.consumerPath}#${site.consumerSymbol}->${site.path}#${site.symbol}:${site.site}`;
}

/** Full-release source classification, not a rendering or quality assertion. */
export function fullReleaseIssues(inventory: SourceInventory, manifest: readonly NamespaceCoverage[] = COVERAGE_MANIFEST): string[] {
  const issues: string[] = [];
  for (const entry of manifest) {
    if (entry.status === 'english-only') issues.push(`full release refuses english-only namespace ${entry.namespace}`);
    for (const identity of entry.qualifiedSurfaces ?? []) {
      if (!inventory.dialogs.some(site => sourceSurfaceIdentity(site) === identity)) issues.push(`declared but not mounted surface ${identity}`);
    }
  }
  for (const route of inventory.routes) {
    const ancestry = `${route.path}#${route.parents?.join('/') || 'root'}:${route.token}`;
    const entry = manifest.find(candidate => candidate.qualifiedRouteTokens?.includes(ancestry))
      ?? classifySourceRoute(route.path, route.token, manifest);
    if (!entry) issues.push(`unclassified source route ${route.path}:${route.token}`);
    else if (entry.status === 'not-applicable' && route.copyFree !== true) issues.push(`not-applicable route has unproved copy-free element ${route.path}:${route.token}`);
  }
  const names = new Set(inventory.dialogs.map(site => site.symbol));
  for (const entry of manifest) for (const surface of entry.surfaces) {
    if (/^[A-Za-z0-9]+Dialog$/.test(surface) && !names.has(surface)) issues.push(`declared but not mounted dialog ${entry.namespace}:${surface}`);
  }
  for (const site of inventory.dialogs) {
    const identity = sourceSurfaceIdentity(site);
    const namespace = consumerNamespace(site.consumerPath, site.consumerSymbol);
    const classified = manifest.some(entry => entry.qualifiedSurfaces?.includes(identity)
      || entry.namespace === namespace && entry.surfaces.includes(site.symbol)
        && entry.surfaceOwners?.[site.symbol] === site.path
        && ['component', 'role:dialog'].includes(site.site));
    if (!classified) issues.push(`unclassified mounted surface ${identity} (consumer namespace ${namespace ?? 'requires exact identity'})`);
  }
  return issues;
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
