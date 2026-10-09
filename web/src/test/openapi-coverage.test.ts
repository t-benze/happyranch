/**
 * Contract test: every daemon route is either in our founder-facing TS client
 * or explicitly excluded as agent-only.
 *
 * Reads the canonical snapshot at ../../../tests/contract/openapi.json and
 * the authoritative route classification at
 * ../../../tests/contract/route-classification.json. When the snapshot
 * changes (a route is added or renamed), this test fails until the engineer
 * either adds the route to the included set or the excluded set (in the JSON
 * fixture) with a justification.
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
const CLASSIFICATION_PATH = resolve(__dirname, '../../../tests/contract/route-classification.json');

interface Snapshot {
  paths: Record<string, Record<string, unknown>>;
}

interface RouteClassification {
  included: string[];
  excluded: Record<string, string>;
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

function loadClassification(): RouteClassification {
  return JSON.parse(readFileSync(CLASSIFICATION_PATH, 'utf-8')) as RouteClassification;
}

const classification = loadClassification();
const INCLUDED_PATHS = new Set<string>(classification.included);
const EXCLUDED_PATHS = new Map<string, string>(Object.entries(classification.excluded));

describe('openapi coverage', () => {
  // Naming snapshot additions were manually derived from identities.py. Actual schema comparison is NOT RUN.
  test('the four existing naming routes are browser-included', () => {
    for (const route of [
      'GET /api/v1/orgs/{slug}/identities',
      'POST /api/v1/orgs/{slug}/identities/resolve',
      'PUT /api/v1/orgs/{slug}/agents/{agent_id}/addressable-name',
      'PUT /api/v1/orgs/{slug}/founder/addressable-name',
    ]) expect(INCLUDED_PATHS.has(route)).toBe(true);
  });
  test('prompt-only founder PUT is browser-included', () => {
    expect(INCLUDED_PATHS.has('PUT /api/v1/orgs/{slug}/agents/{agent_name}/system-prompt')).toBe(true);
  });

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

  test('Usage v1 read routes are browser-included', () => {
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/usage/workload')).toBe(true);
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/usage/efficiency')).toBe(true);
  });

  test('task-detail and dashboard escalation-reason surfaces are browser-included', () => {
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/tasks/{task_id}')).toBe(true);
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/dashboard/summary')).toBe(true);
  });
  test('all initial activation methods are browser-included', () => {
    expect(INCLUDED_PATHS.has('POST /api/v1/orgs/{slug}/workflows/activations')).toBe(true);
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/workflows/activations')).toBe(true);
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/workflows/activations/{activation_id}')).toBe(true);
  });

  test('all cutover methods are browser-included', () => {
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/workflows/cutover')).toBe(true);
    expect(INCLUDED_PATHS.has('POST /api/v1/orgs/{slug}/workflows/cutover/requests')).toBe(true);
    expect(INCLUDED_PATHS.has('GET /api/v1/orgs/{slug}/workflows/cutover/downgrade-preflight')).toBe(true);
  });

});
