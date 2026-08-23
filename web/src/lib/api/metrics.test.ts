import { describe, it, expect } from 'vitest';
import {
  parseSnapshotRow,
  snapshotUsesTemplateLabels,
  SNAPSHOT_FORMAT_VERSION,
} from './metrics';

describe('metrics snapshot format version (TASK-5443)', () => {
  it('SNAPSHOT_FORMAT_VERSION is 2', () => {
    expect(SNAPSHOT_FORMAT_VERSION).toBe(2);
  });

  it('snapshotUsesTemplateLabels is true for a versioned payload', () => {
    expect(snapshotUsesTemplateLabels({ format_version: 2 })).toBe(true);
  });

  it('snapshotUsesTemplateLabels is false for a legacy (missing version) payload', () => {
    expect(snapshotUsesTemplateLabels({})).toBe(false);
  });

  it('parseSnapshotRow parses a legacy row (no format_version)', () => {
    const row = {
      id: 1,
      captured_at: '2026-07-04T12:00:00+00:00',
      snapshot_json: JSON.stringify({
        uptime_seconds: 1,
        loops: {},
        http: { 'GET /api/v1/orgs/tourism-org/tasks/TASK-1': { count: 1 } },
        tasks: { pending_and_in_flight: 0 },
        jobs_in_flight: 0,
        executor_sessions_active: 0,
        run_step_queue_depth: 0,
      }),
    };
    const parsed = parseSnapshotRow(row);
    expect(parsed.snapshot).not.toBeNull();
    expect(parsed.snapshot!.format_version).toBeUndefined();
    expect(snapshotUsesTemplateLabels(parsed.snapshot!)).toBe(false);
  });

  it('parseSnapshotRow parses a versioned row (format_version present)', () => {
    const row = {
      id: 2,
      captured_at: '2026-08-23T12:00:00+00:00',
      snapshot_json: JSON.stringify({
        uptime_seconds: 1,
        loops: {},
        http: {},
        tasks: { pending_and_in_flight: 0 },
        jobs_in_flight: 0,
        executor_sessions_active: 0,
        run_step_queue_depth: 0,
        format_version: 2,
      }),
    };
    const parsed = parseSnapshotRow(row);
    expect(parsed.snapshot).not.toBeNull();
    expect(parsed.snapshot!.format_version).toBe(2);
    expect(snapshotUsesTemplateLabels(parsed.snapshot!)).toBe(true);
  });

  it('parseSnapshotRow returns null snapshot for malformed JSON (never guesses)', () => {
    const parsed = parseSnapshotRow({
      id: 3,
      captured_at: '2026-08-23T12:00:00+00:00',
      snapshot_json: '{not json',
    });
    expect(parsed.snapshot).toBeNull();
  });
});
