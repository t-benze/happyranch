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
  // talks (1:1 founder↔agent)
  'POST /api/v1/orgs/{slug}/talks',
  'GET /api/v1/orgs/{slug}/talks',
  'GET /api/v1/orgs/{slug}/talks/{talk_id}',
  'POST /api/v1/orgs/{slug}/talks/{talk_id}/resume',
  'POST /api/v1/orgs/{slug}/talks/{talk_id}/abandon',
  'POST /api/v1/orgs/{slug}/talks/{talk_id}/end',
  'POST /api/v1/orgs/{slug}/talks/{talk_id}/dispatch',
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
  'POST /api/v1/orgs/{slug}/threads/{thread_id}/abandon',
  // agents — founder-facing (enrollment + read-only learnings)
  'GET /api/v1/orgs/{slug}/agents',
  'POST /api/v1/orgs/{slug}/agents/init',
  'GET /api/v1/orgs/{slug}/agents/enrollments',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/approve',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/reject',
  'POST /api/v1/orgs/{slug}/agents/backfill-enrollments',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/{id_or_slug}',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/search',
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
  // learnings writes (legacy + structured)
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings', 'legacy agent learning append'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/', 'agent-only write'],
  ['PUT /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/{id}', 'agent-only write'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/{id}/promote', 'agent-only write'],
  ['POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/reindex', 'agent-only'],
  // thread agent callbacks (require invocation tokens)
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/reply', 'agent invocation token only'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/decline', 'agent invocation token only'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/dispatch', 'agent invocation token only'],
  ['POST /api/v1/orgs/{slug}/threads/{thread_id}/close-out', 'agent invocation token only'],
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
