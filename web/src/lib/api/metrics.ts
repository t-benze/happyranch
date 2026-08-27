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

/** Per-loop tick record from the metrics registry. */
export interface LoopStats {
  last_tick_iso: string;
  interval_seconds: number;
  last_duration_seconds: number;
}

/** The full /metrics payload: registry snapshot + live pull-gauges.
 *  `http` includes a stable aggregate bucket keyed `"__all__"`.
 *  `format_version` is the snapshot-payload format marker: `2` = route-template
 *  labels (`METHOD <matched FastAPI template>`). It is ABSENT on legacy rows
 *  (raw-URL-path labels), so it is optional to keep both shapes parseable.
 *  `host_sessions` (THR-207) is the bounded host-session observability block
 *  present on live snapshots and newer persisted rows; legacy rows may lack it. */
export interface MetricsSnapshot {
  uptime_seconds: number;
  loops: Record<string, LoopStats>;
  http: Record<string, HttpRouteStats>;
  tasks: { pending_and_in_flight: number };
  jobs_in_flight: number;
  executor_sessions_active: number;
  run_step_queue_depth: number;
  format_version?: number;
  host_sessions?: HostSessionBlock;
}

/* --------------------------------------------------------------------- */
/*  Bounded host-session observability block (THR-207).                    */
/*  Mirrors compose_host_sessions_block in runtime/daemon/host_session_store.py. */
/* --------------------------------------------------------------------- */

/** Backend capability probe summary (three-state capability levels). */
export interface HostSessionBackend {
  name: string | null;
  version: string | null;
  healthy: boolean;
  probed_at: number;
  capabilities: Record<string, string>;
  evidence: string | null;
}

/** Live admission / backpressure state from the daemon-wide controller. */
export interface HostSessionAdmission {
  cap: number | null;
  active: number;
  queue_depth: number;
  oldest_wait_seconds: number;
  head_stall_reason: string | null;
  shutdown: boolean;
  admitted_total: number;
  released_total: number;
  cancelled_queued_total: number;
}

/** Live residue census/gate (survivor identities only on authed surfaces). */
export interface HostSessionResidue {
  admission_blocked: boolean;
  block_reason: string | null;
  survivors_count: number;
  survivors?: Array<{
    pid: number;
    start_identity: string;
    backend: string;
    last_seen_at: number;
  }>;
}

/** Per-provenance peak aggregate (kernel values never blended with sampled). */
export interface HostSessionPeakBucket {
  kernel: { max: number | null; count: number };
  sampled: { max: number | null; count: number };
  unavailable_count: number;
}

/** Bounded receipt aggregates + newest-first recent window. */
export interface HostSessionReceipts {
  published_total: number;
  window_size: number;
  by_terminal_reason: Record<string, number>;
  by_cleanup_status: Record<string, number>;
  quiescent_count: number;
  with_residue_count: number;
  cleanup_duration_seconds: { max: number | null; last: number | null };
  peaks: {
    memory_peak_bytes: HostSessionPeakBucket;
    cpu_total_seconds: HostSessionPeakBucket;
    process_peak: HostSessionPeakBucket;
  };
  recent?: HostSessionReceiptSummary[];
}

/** One bounded per-receipt summary (measured values WITH provenance). */
export interface HostSessionReceiptSummary {
  backend: string;
  terminal_reason: string;
  cleanup_status: string;
  cleanup_duration_seconds: number;
  quiescent: boolean;
  wall_time_seconds: number;
  memory_peak_bytes: number | null;
  memory_peak_provenance: string;
  cpu_total_seconds: number | null;
  cpu_total_provenance: string;
  process_peak: number | null;
  process_peak_provenance: string;
  sample_gap_span_seconds: number;
  enforcement_events: string[];
  survivors_count: number;
}

/** The full `host_sessions` block on /metrics (authed). */
export interface HostSessionBlock {
  wired: boolean;
  backend: HostSessionBackend;
  admission: HostSessionAdmission;
  residue: HostSessionResidue;
  receipts: HostSessionReceipts;
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
