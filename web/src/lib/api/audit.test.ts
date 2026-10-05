import { afterEach, describe, expect, it, vi } from 'vitest';
import { listAudit, type AuditResponse, type MemoryCollectionObservation } from './audit';
import * as client from './client';

const unavailable: MemoryCollectionObservation = {
  contract_version: 1, org: 'alpha', boot_id: null, installed_identity: null,
  generation: null, assigned_intents: null, intent_digest: null,
  phase_counts: null, phase_digests: null, active_preparations: null,
  observation_error: 'observer_unavailable', latest_seal_audit_id: null,
  epoch_id: null, epoch_audit_id: null, sampled_at: '2026-10-05T00:00:00+00:00', data_through: null,
};

describe('audit observation mirror', () => {
  afterEach(() => vi.restoreAllMocks());
  it.each([
    { entries: [], next_cursor: null },
    { entries: [], next_cursor: null, memory_collection_observation: null },
    { entries: [], next_cursor: null, memory_collection_observation: unavailable },
    { entries: [], next_cursor: null, memory_collection_observation: {
      ...unavailable, boot_id: 'boot', generation: 0, assigned_intents: 0,
      observation_error: null, data_through: '2026-10-05T00:00:00+00:00',
      installed_identity: {
        source_root: '/source', runtime_root: '/runtime', org_root: '/runtime/orgs/alpha', package_version: '0.1.0',
        python: { executable: '/python', version: '3.14.4', implementation: 'cpython', cache_tag: 'cpython-314' },
        loaded_code: [], files: [], teams_sha256: '0'.repeat(64), cohort: [], profiles: [],
        backend: { mode: 'legacy', name: null, version: null, capabilities: null },
      },
      intent_digest: '0'.repeat(64), active_preparations: [],
      phase_counts: {
        intent: { attempted: 0, persisted: 0 }, identity: { attempted: 0, persisted: 0 },
        expectation: { attempted: 0, persisted: 0 }, binding: { attempted: 0, persisted: 0 },
        launched: { attempted: 0, persisted: 0 }, terminal: { attempted: 0, persisted: 0 },
      },
      phase_digests: {
        intent: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
        identity: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
        expectation: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
        binding: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
        launched: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
        terminal: { attempted: '0'.repeat(64), persisted: '0'.repeat(64) },
      },
    } },
  ] satisfies AuditResponse[])('preserves absence/null/unavailable/known metadata without coercion: %j', async (body) => {
    const request = vi.spyOn(client, 'request').mockResolvedValue(body);
    const result = await listAudit('alpha', { action: 'memory_collection_seal', limit: 2, cursor: 'opaque' });
    expect(result).toEqual(body);
    expect(request).toHaveBeenCalledWith('/orgs/alpha/audit', {
      params: { action: 'memory_collection_seal', limit: 2, cursor: 'opaque', include_thread_origin: true },
    });
    expect(result.memory_collection_observation?.epoch_id ?? null).toBeNull();
  });
  it('preserves ordinary request options and response shape', async () => {
    const body = { entries: [], next_cursor: 'cursor' };
    const request = vi.spyOn(client, 'request').mockResolvedValue(body);
    expect(await listAudit('alpha', { action: 'session_start', include_thread_origin: false })).toEqual(body);
    expect(request).toHaveBeenCalledWith('/orgs/alpha/audit', {
      params: { action: 'session_start', include_thread_origin: false },
    });
  });
});
