/**
 * Mirror of runtime/daemon/routes/metrics.py (THR-066).
 *
 * Two bearer-authed, daemon-global routes (NOT org-scoped):
 *   - GET /api/v1/metrics          — live snapshot + pull-gauges
 *   - GET /api/v1/metrics/history  — persisted snapshot rows, newest-first
 *
 * Honesty fence: the shapes below mirror the EXACT server payloads
 * (compose_metrics_snapshot + MetricsRegistry.snapshot). No invented fields.
 */
import { request } from './client';

/** Per-route latency histogram summary. `p*`/`max` are SECONDS, null when
 *  the route has recorded zero samples. `count` is the sample count. */
export interface HttpRouteStats {
  count: number;
  p50: number | null;
  p95: number | null;
  max: number | null;
}

/** Snapshot format version. `2` marks route-TEMPLATE `http` labels (bounded
 *  cardinality). Legacy rows predate this field and used raw URL-path labels;
 *  a MISSING `format_version` therefore means legacy/raw-label format. */
export const SNAPSHOT_FORMAT_VERSION = 2;

/** Per-loop tick record from the metrics registry. */
export interface LoopStats {
  last_tick_iso: string;
  interval_seconds: number;
  last_duration_seconds: number;
}

/** The full /metrics payload: registry snapshot + live pull-gauges.
 *  `http` includes a stable aggregate bucket keyed `"__all__"`. */
export interface MetricsSnapshot {
  uptime_seconds: number;
  loops: Record<string, LoopStats>;
  http: Record<string, HttpRouteStats>;
  tasks: { pending_and_in_flight: number };
  jobs_in_flight: number;
  executor_sessions_active: number;
  run_step_queue_depth: number;
  /** Present on current snapshots (=== {@link SNAPSHOT_FORMAT_VERSION});
   *  absent on legacy rows that used raw URL-path `http` labels. */
  format_version?: number;
}

/** True when a snapshot carries route-template labels (version 2+); false for
 *  legacy rows whose `http` labels were raw URL paths (missing version).
 *  Accepts a full snapshot or just its `format_version` field. */
export function snapshotUsesTemplateLabels(
  snapshot: Pick<MetricsSnapshot, 'format_version'>,
): boolean {
  return snapshot.format_version !== undefined;
}

/** One persisted history row. `snapshot_json` is a JSON-encoded
 *  MetricsSnapshot string (parse with {@link parseSnapshotRow}). */
export interface MetricsHistoryRow {
  id: number;
  captured_at: string;
  snapshot_json: string;
}

export interface MetricsHistoryResponse {
  snapshots: MetricsHistoryRow[];
}

/** A history row whose `snapshot_json` has been parsed, or null when the row
 *  is unparseable (defensive — never fabricate a shape). */
export interface ParsedHistoryRow {
  id: number;
  captured_at: string;
  snapshot: MetricsSnapshot | null;
}

export interface MetricsHistoryQuery {
  since?: string;
  until?: string;
  limit?: number;
}

export const getMetrics = (): Promise<MetricsSnapshot> => request('/metrics');

export const getMetricsHistory = (
  params: MetricsHistoryQuery = {},
): Promise<MetricsHistoryResponse> =>
  // Pass an inline literal (not the interface-typed value) so it satisfies the
  // client's Record<string, …> params index signature.
  request('/metrics/history', {
    params: { since: params.since, until: params.until, limit: params.limit },
  });

/** Parse a raw history row's `snapshot_json`. Returns snapshot=null (never a
 *  guessed shape) if the JSON is malformed — the honest degrade path. */
export function parseSnapshotRow(row: MetricsHistoryRow): ParsedHistoryRow {
  let snapshot: MetricsSnapshot | null = null;
  try {
    snapshot = JSON.parse(row.snapshot_json) as MetricsSnapshot;
  } catch {
    snapshot = null;
  }
  return { id: row.id, captured_at: row.captured_at, snapshot };
}
