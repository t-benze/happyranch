import type { DreamRecord, DreamKbCandidate } from '@/lib/api/dreams';
import type {
  DreamsApi,
  DreamsRoutes,
  MutationLike,
  QueryLike,
} from './DataContext';

/* ------------------------------------------------------------------ */
/*  Mock data                                                          */
/* ------------------------------------------------------------------ */

const MOCK_DREAMS: DreamRecord[] = [
  {
    dream_id: 'DREAM-0012',
    agent_name: 'engineering_manager',
    local_date: '2026-06-18',
    scheduled_for: '2026-06-18T03:00:00Z',
    window_start: '2026-06-18T02:55:00Z',
    window_end: '2026-06-18T03:10:00Z',
    started_at: '2026-06-18T03:00:05Z',
    ended_at: '2026-06-18T03:03:42Z',
    status: 'completed',
    summary: 'Routine nightly reflection. Reviewed task throughput and token costs. No escalations needed.',
    transcript_path: null,
    new_learnings_count: 2,
    kb_candidate_count: 0,
    founder_thread_id: null,
    error: null,
  },
  {
    dream_id: 'DREAM-0011',
    agent_name: 'product_lead',
    local_date: '2026-06-18',
    scheduled_for: '2026-06-18T03:00:00Z',
    window_start: '2026-06-18T02:55:00Z',
    window_end: '2026-06-18T03:10:00Z',
    started_at: '2026-06-18T03:00:02Z',
    ended_at: '2026-06-18T03:05:11Z',
    status: 'completed',
    summary: 'Identified a recurring pattern in Spanish walk-in handling. Proposed adding a routing guide as a KB entry.',
    transcript_path: null,
    new_learnings_count: 1,
    kb_candidate_count: 2,
    founder_thread_id: 'THR-010',
    error: null,
  },
  {
    dream_id: 'DREAM-0010',
    agent_name: 'qa_engineer',
    local_date: '2026-06-17',
    scheduled_for: '2026-06-17T03:00:00Z',
    window_start: '2026-06-17T02:55:00Z',
    window_end: '2026-06-17T03:10:00Z',
    started_at: '2026-06-17T03:00:10Z',
    ended_at: '2026-06-17T03:04:30Z',
    status: 'completed',
    summary: 'Reviewed recent test runs. Two flaky tests noted — added a learning about PTY echo race.',
    transcript_path: null,
    new_learnings_count: 1,
    kb_candidate_count: 0,
    founder_thread_id: null,
    error: null,
  },
  {
    dream_id: 'DREAM-0009',
    agent_name: 'dev_agent',
    local_date: '2026-06-17',
    scheduled_for: '2026-06-17T03:00:00Z',
    window_start: '2026-06-17T02:55:00Z',
    window_end: '2026-06-17T03:10:00Z',
    started_at: '2026-06-17T03:00:07Z',
    ended_at: null,
    status: 'failed',
    summary: null,
    transcript_path: null,
    new_learnings_count: 0,
    kb_candidate_count: 0,
    founder_thread_id: null,
    error: 'Executor API returned 503 — temporary outage',
  },
  {
    dream_id: 'DREAM-0008',
    agent_name: 'engineering_manager',
    local_date: '2026-06-17',
    scheduled_for: '2026-06-17T03:00:00Z',
    window_start: '2026-06-17T02:55:00Z',
    window_end: '2026-06-17T03:10:00Z',
    started_at: null,
    ended_at: null,
    status: 'missed',
    summary: null,
    transcript_path: null,
    new_learnings_count: 0,
    kb_candidate_count: 0,
    founder_thread_id: null,
    error: null,
  },
];

const MOCK_CANDIDATES: DreamKbCandidate[] = [
  {
    id: 1,
    dream_id: 'DREAM-0011',
    agent_name: 'product_lead',
    slug: 'routing/spanish-after-hours',
    title: 'Spanish after-hours routing pattern',
    topic: 'routing',
    rationale: 'Recurring pattern observed over 3 weeks. Formalizing as a KB entry will save coordination time.',
    body_markdown: '# Spanish after-hours routing\n\nWhen a Spanish-speaking walk-in arrives after 18:00, route immediately to the partner concierge. The bilingual intake agent is only available during daytime hours.\n\n## Procedure\n1. Greet in Spanish.\n2. Offer immediate partner handoff.\n3. Log the handoff in intake tracker.',
    status: 'pending',
    promoted_kb_slug: null,
    created_at: '2026-06-18T03:05:11Z',
    updated_at: '2026-06-18T03:05:11Z',
  },
  {
    id: 2,
    dream_id: 'DREAM-0011',
    agent_name: 'product_lead',
    slug: 'policy/macau-booking-deposit',
    title: 'Macau booking deposit policy',
    topic: 'finance',
    rationale: 'Multiple threads this week discussed deposit amounts. Standardizing avoids re-discussion.',
    body_markdown: '# Macau booking deposit\n\nStandard deposit for Macau venue bookings is **MOP 2,000**. Refundable up to 48h before the event.\n\n## Exceptions\n- Corporate clients: MOP 5,000 deposit\n- Repeat customers with >3 bookings: deposit waived',
    status: 'pending',
    promoted_kb_slug: null,
    created_at: '2026-06-18T03:05:11Z',
    updated_at: '2026-06-18T03:05:11Z',
  },
  {
    id: 3,
    dream_id: 'DREAM-0011',
    agent_name: 'product_lead',
    slug: 'intake/macau-after-hours',
    title: 'Macau after-hours intake SOP',
    topic: 'intake',
    rationale: 'Documenting the current practice. Already in use informally.',
    body_markdown: '# Macau after-hours intake\n\nAfter 20:00 Macau time, all intake requests route to the partner concierge. The intake agent is offline.\n\n## Handoff\n1. Collect name + phone.\n2. Forward to partner concierge.\n3. Confirm receipt.',
    status: 'rejected',
    promoted_kb_slug: null,
    created_at: '2026-06-18T03:05:11Z',
    updated_at: '2026-06-18T03:06:00Z',
  },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function ok<T>(data: T): QueryLike<T> {
  return { data, isLoading: false, isError: false, error: null };
}

function noopMutation<TArg, TResult>(): MutationLike<TArg, TResult> {
  return {
    mutateAsync: async () => ({} as TResult),
    isPending: false,
  };
}

/* ------------------------------------------------------------------ */
/*  API object                                                         */
/* ------------------------------------------------------------------ */

export const mockDreamsApi: DreamsApi = {
  useDreamsList: (params) =>
    ok({
      dreams: params?.agent
        ? MOCK_DREAMS.filter((d) => d.agent_name === params.agent)
        : MOCK_DREAMS,
    }),
  useDream: (dreamId) =>
    ok({
      ...(MOCK_DREAMS.find((d) => d.dream_id === dreamId) ?? MOCK_DREAMS[0]),
      transcript: dreamId === 'DREAM-0011'
        ? '## Reflection\n\nIdentified a recurring pattern in Spanish walk-in handling after hours. The intake team has been handling this ad-hoc; formalizing as a KB entry will save coordination time and reduce errors.\n\nProposed: a routing guide entry + a Spanish after-hours SOP.'
        : dreamId === 'DREAM-0012'
          ? '## Reflection\n\nRoutine review of task throughput. Token costs are within normal range. No escalations or new patterns identified.'
          : undefined,
      kb_candidates: dreamId === 'DREAM-0011'
        ? MOCK_CANDIDATES
        : dreamId
          ? MOCK_CANDIDATES.filter((c) => c.dream_id === dreamId)
          : [],
    }),
  useAcceptCandidate: () => noopMutation<number, DreamKbCandidate>(),
  useDismissCandidate: () => noopMutation<number, DreamKbCandidate>(),
};

export function useMockDreamsRoutes(): DreamsRoutes {
  return {
    inbox: () => '/__prototypes/dreams',
    detail: (dreamId: string) => `/__prototypes/dreams/${dreamId}`,
    inboxForOrg: () => '/__prototypes/dreams',
  };
}
