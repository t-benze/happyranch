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
 * `src/routes.tsx`, `src/prototypes/index.tsx` and the settings sub-route tree.
 * `surfaces` documents reachable shared dialogs/overlays that belong to the
 * namespace (they are inspected in the W2-W4 migration, not discovered by the
 * route-token scan).
 */
import type { Locale } from './locale';
import { translate } from './catalog';

export type CoverageStatus = 'translated' | 'english-only' | 'not-applicable';

export interface NamespaceCoverage {
  namespace: string;
  routeTokens: readonly string[];
  status: CoverageStatus;
  surfaces: readonly string[];
}

export const COVERAGE_MANIFEST: readonly NamespaceCoverage[] = [
  {
    namespace: 'redirects',
    routeTokens: ['index', '/orgs/:slug', 'spend', 'schedule', '*'],
    status: 'not-applicable',
    surfaces: [
      'RootRedirect',
      'OrgLayout (org-context wrapper, no copy)',
      'SpendRedirect',
      'ScheduleRedirect',
      'NotFound',
      'settings catch-alls',
    ],
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
    surfaces: ['ThreadsPage', 'ThreadDetailPane', 'NewThreadDialog', 'ArchiveDialog', 'AbandonDialog'],
  },
  {
    namespace: 'tasks',
    routeTokens: ['tasks', 'tasks/:task_id'],
    status: 'english-only',
    surfaces: ['TasksPage', 'TaskDetailPage', 'TaskCancelDialog', 'TaskRevisitDialog'],
  },
  {
    namespace: 'todos',
    routeTokens: ['todos', 'todos/:scheduleId'],
    status: 'english-only',
    surfaces: ['TodosPage'],
  },
  {
    namespace: 'kb',
    routeTokens: ['kb', 'kb/:entrySlug/*'],
    status: 'english-only',
    surfaces: ['KbPage', 'KbComposer'],
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
    surfaces: ['SkillsPage', 'SkillValidationPage', 'SkillDetailPage', 'CustomSkillsPage'],
  },
  {
    namespace: 'agents',
    routeTokens: ['agents', 'agents/:agent_name', 'agents/:agent_name/team-escalation-policy'],
    status: 'english-only',
    surfaces: ['AgentsPage', 'AddAgentDialog', 'TeamEscalationPolicyPage'],
  },
  {
    namespace: 'jobs',
    routeTokens: ['jobs', 'jobs/:job_id'],
    status: 'english-only',
    surfaces: ['JobsPage', 'JobDetailPage', 'JobReviewDialog'],
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
    surfaces: ['WorkHoursOverviewPage', 'WakesView', 'AgentDetailPage'],
  },
  {
    namespace: 'artifacts',
    routeTokens: ['artifacts'],
    status: 'english-only',
    surfaces: ['ArtifactsPage'],
  },
  {
    namespace: 'settings',
    routeTokens: [
      'settings/*',
      'assistant',
      'daemon-capacity',
      'organization',
      'executors',
      'system',
    ],
    status: 'english-only',
    surfaces: [
      'SettingsPage',
      'SettingsDialog',
      'PreferencesSection (W2)',
      'settings system/agents/* redirects',
    ],
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

function buildTokenIndex(): Map<string, NamespaceCoverage> {
  const index = new Map<string, NamespaceCoverage>();
  for (const entry of COVERAGE_MANIFEST) {
    for (const token of entry.routeTokens) {
      if (!index.has(token)) index.set(token, entry);
    }
  }
  return index;
}

const TOKEN_INDEX = buildTokenIndex();

export function classifyRouteToken(token: string): NamespaceCoverage | undefined {
  return TOKEN_INDEX.get(token);
}

/** Tokens that the manifest does not classify (the check's failure signal). */
export function unclassifiedRouteTokens(tokens: readonly string[]): string[] {
  return tokens.filter((token) => !TOKEN_INDEX.has(token));
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
 * Source-scan helper: extract the literal `path="..."` tokens from a route
 * module's source. Used by `coverage.test.ts` against `routes.tsx`,
 * `prototypes/index.tsx` and `SettingsPage.tsx`.
 */
export function extractRouteTokens(source: string): string[] {
  const tokens: string[] = [];
  for (const match of source.matchAll(/path="([^"]*)"/g)) {
    if (!tokens.includes(match[1])) tokens.push(match[1]);
  }
  return tokens;
}
