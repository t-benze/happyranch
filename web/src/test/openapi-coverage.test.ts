/**
 * Contract test: every daemon route is either in our founder-facing TS client
 * or explicitly excluded as agent-only.
 *
 * Reads the canonical snapshot at ../../../tests/contract/openapi.json. When
 * the snapshot changes (a route is added or renamed), this test fails until
 * the engineer either:
 *   - adds the route to INCLUDED_PATHS (and writes the TS function), or
 *   - adds it to EXCLUDED_PATHS with a justification.
 *
 * The two sets must collectively equal every documented path. No leftover,
 * no extras.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const SNAPSHOT_PATH = resolve(__dirname, '../../../tests/contract/openapi.json');

interface Snapshot {
  paths: Record<string, Record<string, unknown>>;
}

function loadDaemonRoutes(): Set<string> {
  const snap = JSON.parse(readFileSync(SNAPSHOT_PATH, 'utf-8')) as Snapshot;
  const out = new Set<string>();
  for (const [path, methods] of Object.entries(snap.paths)) {
    for (const method of Object.keys(methods)) {
      out.add(`${method.toUpperCase()} ${path}`);
    }
  }
  return out;
}

/**
 * Founder-facing routes the TS client mirrors. Each entry MUST have a
 * corresponding function in some lib/api/*.ts module.
 */
const INCLUDED_PATHS = new Set<string>([
  // health + auth
  'GET /api/v1/health',
  'GET /api/v1/auth/bootstrap',
  // runtime
  'GET /api/v1/runtime',
  'POST /api/v1/runtime',
  'POST /api/v1/runtime/use',
  // orgs (container-level)
  'GET /api/v1/orgs',
  'POST /api/v1/orgs',
  'DELETE /api/v1/orgs/{slug}',
  // tasks
  'POST /api/v1/orgs/{slug}/tasks',
  'GET /api/v1/orgs/{slug}/tasks',
  'GET /api/v1/orgs/{slug}/tasks/roots',
  'GET /api/v1/orgs/{slug}/tasks/{task_id}',
  'GET /api/v1/orgs/{slug}/tasks/{task_id}/recall',
  'GET /api/v1/orgs/{slug}/tasks/{task_id}/events',
  'POST /api/v1/orgs/{slug}/tasks/{task_id}/resolve-escalation',
  'POST /api/v1/orgs/{slug}/tasks/{task_id}/revisit',
  'POST /api/v1/orgs/{slug}/tasks/{task_id}/cancel',
  // audit + tokens
  'GET /api/v1/orgs/{slug}/audit',
  'GET /api/v1/orgs/{slug}/tokens',
  // kb (founder-facing — adds/updates/deletes flow through here)
  'GET /api/v1/orgs/{slug}/kb',
  'POST /api/v1/orgs/{slug}/kb',
  'POST /api/v1/orgs/{slug}/kb/reindex',
  'GET /api/v1/orgs/{slug}/kb/search',
  'GET /api/v1/orgs/{slug}/kb/{entry_slug}',
  'POST /api/v1/orgs/{slug}/kb/{entry_slug}',
  'DELETE /api/v1/orgs/{slug}/kb/{entry_slug}',
  // threads — founder-facing only
  'POST /api/v1/orgs/{slug}/threads',
  'GET /api/v1/orgs/{slug}/threads',
  'GET /api/v1/orgs/{slug}/threads/events',
  'GET /api/v1/orgs/{slug}/threads/{thread_id}',
  'GET /api/v1/orgs/{slug}/threads/{thread_id}/messages',
  'GET /api/v1/orgs/{slug}/threads/{thread_id}/tail',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/send',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/invite',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/extend',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/archive',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/abort-replies',
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/resume',
  // jobs — founder-facing
  'GET /api/v1/orgs/{slug}/jobs/',
  'GET /api/v1/orgs/{slug}/jobs/{job_id}',
  'POST /api/v1/orgs/{slug}/jobs/{job_id}/run',
  'POST /api/v1/orgs/{slug}/jobs/{job_id}/reject',
  'GET /api/v1/orgs/{slug}/jobs/{job_id}/output',
  'GET /api/v1/orgs/{slug}/jobs/{job_id}/events',
  'GET /api/v1/orgs/{slug}/jobs/{job_id}/tail',
  'POST /api/v1/orgs/{slug}/jobs/{job_id}/wait',
  'POST /api/v1/orgs/{slug}/jobs/{job_id}/stop',
  // dreams — founder-facing list/show/status; completion is agent callback;
  // accept/dismiss are browser-callable for the candidate review gate
  'GET /api/v1/orgs/{slug}/dreams/status',
  'GET /api/v1/orgs/{slug}/dreams',
  'GET /api/v1/orgs/{slug}/dreams/{dream_id}',
  'POST /api/v1/orgs/{slug}/dreams/candidates/{candidate_id}/accept',
  'POST /api/v1/orgs/{slug}/dreams/candidates/{candidate_id}/dismiss',
  // work-hours — founder-facing list/show/status; spawn is agent callback
  'GET /api/v1/orgs/{slug}/work-hours/status',
  'GET /api/v1/orgs/{slug}/work-hours',
  'GET /api/v1/orgs/{slug}/work-hours/next-wakes',
  'GET /api/v1/orgs/{slug}/work-hours/{work_hour_id}',
  // dashboard — founder-facing summary rollup
  'GET /api/v1/orgs/{slug}/dashboard/summary',
  // agents — founder-facing (enrollment + read-only memory)
  'GET /api/v1/orgs/{slug}/agents',
  'POST /api/v1/orgs/{slug}/agents',
  'POST /api/v1/orgs/{slug}/agents/init',
  'GET /api/v1/orgs/{slug}/agents/enrollments',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/approve',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/reject',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/{id_or_slug}',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/search',
  // agent model — set via the AgentDetailPane Model input (THR-067 PR-2)
  'PUT /api/v1/orgs/{slug}/agents/{agent_name}/model',
  // teams — founder-facing
  'GET /api/v1/orgs/{slug}/teams',
  // system assistant — founder-facing setup surfaced in the SPA
  // (web/src/features/system-assistant) via lib/api/assistant.ts. The
  // WebSocket PTY at /assistant/session is not in OpenAPI (FastAPI omits WS).
  'GET /api/v1/assistant/status',
  'GET /api/v1/assistant/a-mode/status',
  'GET /api/v1/assistant/a-mode/conversations',
  'POST /api/v1/assistant/a-mode/conversations',
  'POST /api/v1/assistant/a-mode/conversations/{conv_id}/activate',
  'PATCH /api/v1/assistant/a-mode/conversations/{conv_id}',
  'DELETE /api/v1/assistant/a-mode/conversations/{conv_id}',
  'POST /api/v1/assistant/init',
  'POST /api/v1/assistant/register',
  'POST /api/v1/assistant/repair',
  // artifacts — founder artifacts UI delete (mirror: deleteArtifact in lib/api/artifacts.ts)
  'DELETE /api/v1/orgs/{slug}/artifacts/{name}',
  // kb view stats — now wired into the SPA for the "viewed Nx (CLI)" label (PRD §4.5 K1)
  'GET /api/v1/orgs/{slug}/kb/stats',
  // settings — founder-facing read-only System + Org settings (Phase 1)
  'GET /api/v1/orgs/{slug}/settings',
  // settings — editable org settings (Phase 2)
  'PUT /api/v1/orgs/{slug}/settings/org',
  // settings — teams membership editing (Phase 2)
  'PUT /api/v1/orgs/{slug}/settings/teams',
]);

/**
 * Agent-callback or otherwise-not-browser-facing routes. The TS client must
 * NOT expose these (they'd be a privilege escalation or invocation-token
 * forge). Spec §2 enumerates the rationale.
 */
const EXCLUDED_PATHS = new Map<string, string>([
  // task agent callbacks
  ['POST /api/v1/orgs/{slug}/tasks/{task_id}/completion', 'agent callback'],
  ['POST /api/v1/orgs/{slug}/tasks/{task_id}/progress', 'agent callback'],
  // agent self-service
  ['POST /api/v1/orgs/{slug}/agents/manage', 'agent enrollment via skill'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/repos', 'agent manage-repo callback'],
  // founder set-executor — CLI-only (happyranch set-executor); not wired into the SPA
  ['PUT /api/v1/orgs/{slug}/agents/{agent_name}/executor', 'founder CLI set-executor only; not in SPA'],
  // memory writes (legacy + structured)
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/memory', 'legacy agent memory append'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/', 'agent-only write'],
  ['PUT /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/{id}', 'agent-only write'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/{id}/promote', 'agent-only write'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/reindex', 'agent-only'],
  ['PATCH /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/{id}/lifecycle', 'agent-only write'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/memory/entries/compact', 'agent-only compaction'],
  // thread agent callbacks (require invocation tokens)
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/reply', 'agent invocation token only'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/decline', 'agent invocation token only'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/dispatch', 'agent invocation token only'],
  // agent-initiated thread compose (agent callback — not exercised from the Web UI)
  ['POST /api/v1/orgs/{slug}/threads/compose-as-agent', 'agent callback — not exercised from the Web UI'],
  // task-session post into an existing thread (agent callback — not exercised from the Web UI)
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/post-as-agent', 'agent callback — not exercised from the Web UI'],
  // Thread-scoped attachments (TASK-1616) — agent/CLI facing; no SPA wrapper yet
  ['GET /api/v1/orgs/{slug}/threads/{thread_id}/attachments', 'thread-scoped attachments — agent/CLI facing'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/attachments', 'thread-scoped attachments — agent/CLI facing'],
  ['GET /api/v1/orgs/{slug}/threads/{thread_id}/attachments/{attachment_id}', 'thread-scoped attachments — agent/CLI facing'],
  // jobs agent callback
  ['POST /api/v1/orgs/{slug}/jobs/submit', 'agent callback (matches /report-completion pattern)'],
  // Artifacts — agent-facing v1, also surfaced read+create in the founder
  // artifacts UI (web/src/features/artifacts) via uploadArtifact() / listArtifacts() /
  // artifactDownloadPath(). The DELETE route is browser-facing (mirrored by
  // deleteArtifact(); listed in the founder-facing set above), so it is not
  // excluded here. No update route exists (POST is an idempotent
  // create-or-overwrite; backend-gated).
  ['POST /api/v1/orgs/{slug}/artifacts', 'agent-facing v1; also founder artifacts UI upload'],
  ['GET /api/v1/orgs/{slug}/artifacts', 'agent-facing v1; also founder artifacts UI list'],
  ['GET /api/v1/orgs/{slug}/artifacts/{name}', 'agent-facing v1; also founder artifacts UI download'],
  // dreams agent callback
  ['POST /api/v1/orgs/{slug}/dreams/{dream_id}/complete', 'agent callback'],
  // work-hours wake spawn — agent callback (single-line --from-file), not browser-callable
  ['POST /api/v1/orgs/{slug}/work-hours/{work_hour_id}/spawn', 'agent callback'],
  // registration-token mint — founder-only (master-bearer + loopback); minted by the
  // PR-3 settings-page generator, no SPA client wrapper yet — promote to INCLUDED_PATHS
  // when PR-3 adds the wrapper
  ['POST /api/v1/auth/registration-token', 'founder-only registration-token mint; PR-3 will add SPA wrapper'],
  // executor conformance check-in — CLI-only, scoped-token-only (THR-052 PR-2);
  // the candidate CLI calls this to record conformance step arrivals
  ['POST /api/v1/orgs/{slug}/executors/conformance-checkin', 'cli-only conformance check-in (THR-052 PR-2)'],
  // executor registration — scoped-token-only (THR-052 PR-2);
  // conformance-gated, daemon-verified write of executor_profiles to config
  ['POST /api/v1/orgs/{slug}/executors/register', 'conformance-gated executor registration (THR-052 PR-2)'],
  // operational metrics — agent/CLI facing; web dashboard panel deferred (THR-066 follow-up)
  ['GET /api/v1/metrics', 'operational metrics — agent/CLI facing; web dashboard panel deferred (THR-066 follow-up)'],
  ['GET /api/v1/metrics/history', 'operational metrics history — agent/CLI facing (THR-066 PR-2)'],

]);

describe('openapi coverage', () => {
  const daemonRoutes = loadDaemonRoutes();

  test('every daemon route is either included or excluded', () => {
    const missing: string[] = [];
    for (const r of daemonRoutes) {
      const inI = INCLUDED_PATHS.has(r);
      const inE = EXCLUDED_PATHS.has(r);
      if (inI && inE) {
        missing.push(`DUPLICATE (in both sets): ${r}`);
      } else if (!inI && !inE) {
        missing.push(`UNCLASSIFIED: ${r}`);
      }
    }
    if (missing.length) {
      throw new Error(
        `OpenAPI coverage failure — add to INCLUDED_PATHS or EXCLUDED_PATHS in this file:\n` +
          missing.map((m) => '  ' + m).join('\n'),
      );
    }
  });

  test('no included paths reference removed daemon routes', () => {
    const stale = [...INCLUDED_PATHS].filter((r) => !daemonRoutes.has(r));
    expect(stale, `Stale INCLUDED entries (route no longer in openapi.json): ${stale.join(', ')}`)
      .toEqual([]);
  });

  test('no excluded paths reference removed daemon routes', () => {
    const stale = [...EXCLUDED_PATHS.keys()].filter((r) => !daemonRoutes.has(r));
    expect(stale, `Stale EXCLUDED entries: ${stale.join(', ')}`).toEqual([]);
  });
});
