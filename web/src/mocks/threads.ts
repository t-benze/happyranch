/**
 * Mock thread index for the prototype harness.
 *
 * Deterministic — every value is a hand-written literal. No `Date.now()`,
 * no `Math.random()`. Visual regression diffs against these fixtures are
 * meant to be stable across hot-reload and re-renders.
 *
 * IDs follow the production scheme (`THR-NNN`); agent names follow the
 * sample org (`engineering_head`, `support_lead`, `ops_lead`) so the
 * prototype routes feel like they belong to the same product.
 */
import type { ThreadRecord } from '@/lib/api/types';

export const MOCK_THREADS: ThreadRecord[] = [
  {
    thread_id: 'THR-001',
    subject: 'Q4 venue research — Macau pavilions',
    status: 'open',
    started_at: '2026-05-12T09:14:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 28,
    summary: null,
    transcript_path: null,
  },
  {
    thread_id: 'THR-002',
    subject: 'Macau ferry schedule update for VIP itinerary',
    status: 'open',
    started_at: '2026-05-13T16:02:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 84,
    summary: null,
    transcript_path: null,
  },
  {
    thread_id: 'THR-003',
    subject: 'VIP itinerary draft — Lan Kwai Fong + Sheung Wan',
    status: 'open',
    started_at: '2026-05-14T08:30:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 412,
    summary: null,
    transcript_path: null,
  },
  {
    thread_id: 'THR-004',
    subject: 'Refund policy clarification — Group A',
    status: 'archived',
    started_at: '2026-05-04T11:45:00Z',
    archived_at: '2026-05-09T18:22:00Z',
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 17,
    summary: 'Group A refund honored, KB updated; SOP unchanged.',
    transcript_path: 'threads/THR-004.md',
  },
];
