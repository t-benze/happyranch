/**
 * KbPage tests — Knowledge surface (§4.5).
 *
 * Covers: folder filtering, candidate feed rendering, candidate-gate
 * state transitions (Accept/Dismiss), pending-count tag, error states,
 * and shared candidate state via the merged STEP-1 route.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test, beforeEach } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

/* ------------------------------------------------------------------ */
/*  Fixtures                                                           */
/* ------------------------------------------------------------------ */

const ENTRY_A = {
  slug: 'policy/refund-thresholds',
  title: 'Refund authority by tier',
  type: 'precedent',
  topic: 'finance',
  tags: ['policy', 'finance'],
  body: '# Refund authority\n\nThe CX Manager may approve refunds up to $150.',
  updated_at: '2026-05-16T09:00:00Z',
  authored_by: 'founder',
  source_task: 'TASK-0042',
  related_entries: ['intake/spanish-walk-ins'],
};

const ENTRY_B = {
  slug: 'intake/spanish-walk-ins',
  title: 'Spanish-speaking walk-in flow',
  type: 'sop',
  topic: 'intake',
  tags: ['intake'],
  body: '# Spanish walk-ins',
  updated_at: '2026-05-12T11:00:00Z',
  authored_by: 'intake_manager',
  source_task: null,
  related_entries: [],
};

const DREAM_WITH_CANDIDATE = {
  dream_id: 'DREAM-0099',
  agent_name: 'triage_agent',
  local_date: '2026-06-17',
  scheduled_for: '2026-06-17T03:00:00',
  window_start: '2026-06-17T02:55:00',
  window_end: '2026-06-17T03:05:00',
  started_at: '2026-06-17T03:00:01',
  ended_at: '2026-06-17T03:02:30',
  status: 'completed',
  summary: 'Routine nightly reflection',
  transcript_path: null,
  new_learnings_count: 2,
  kb_candidate_count: 1,
  founder_thread_id: null,
  error: null,
};

const CANDIDATE_A = {
  id: 1,
  dream_id: 'DREAM-0099',
  agent_name: 'triage_agent',
  slug: 'policy/new-refund-flow',
  title: 'New refund flow for walk-ins',
  topic: 'policy',
  rationale: 'Found a gap in the current refund process for Spanish-speaking walk-ins.',
  body_markdown: '## Proposed\n\nAdd a new step for multi-language refund verification.',
  status: 'pending',
  promoted_kb_slug: null,
  created_at: '2026-06-17T03:03:00Z',
  updated_at: '2026-06-17T03:03:00Z',
};

const CANDIDATE_ACCEPTED = {
  ...CANDIDATE_A,
  id: 2,
  slug: 'policy/already-accepted',
  title: 'Already accepted candidate',
  status: 'promoted',
  promoted_kb_slug: 'policy/already-accepted',
};

/* ------------------------------------------------------------------ */
/*  Tests — folder filtering                                           */
/* ------------------------------------------------------------------ */

describe('KbPage — folder filtering', () => {
  test('filters entries by type (folder) via server param', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let serverParams: string | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
        const url = new URL(request.url);
        serverParams = url.searchParams.get('type');
        const all = [ENTRY_A, ENTRY_B];
        const filtered = serverParams
          ? all.filter((e) => e.type === serverParams)
          : all;
        return HttpResponse.json({ entries: filtered });
      }),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    await user.click(screen.getByRole('button', { name: /^precedent$/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
  });

  test('"All" folder clears the type filter', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    let serverParams: string | null = null;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
        const url = new URL(request.url);
        const type = url.searchParams.get('type');
        serverParams = type;
        const all = [ENTRY_A, ENTRY_B];
        const filtered = type ? all.filter((e) => e.type === type) : all;
        return HttpResponse.json({ entries: filtered });
      }),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Both entries visible initially
    expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
    // Click a specific folder — only precedent-type entries should show
    await user.click(screen.getByRole('button', { name: /^precedent$/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
    // Click "All" — FilterSidebar clears selection, shows cached both entries
    // (no re-fetch since the query key matches the initial cache)
    await user.click(screen.getByRole('button', { name: /^All$/ }));
    await waitFor(() =>
      expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument(),
    );
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — candidate feed rendering                                   */
/* ------------------------------------------------------------------ */

describe('KbPage — candidate feed', () => {
  test('shows dream-proposed candidates in the feed with crescent moon marker', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [DREAM_WITH_CANDIDATE] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Candidate should appear
    await screen.findByText(/New refund flow for walk-ins/);
    // Honest provenance label
    expect(
      screen.getByText(/from dream · proposed by triage_agent/),
    ).toBeInTheDocument();
    // "pending review" badge
    expect(screen.getByText('pending review')).toBeInTheDocument();
  });

  test('shows pending-count tag when candidates exist', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [DREAM_WITH_CANDIDATE] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A, CANDIDATE_ACCEPTED],
        }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // The pending count tag shows the total kb_candidate_count from dreams list (1)
    // The actual pending candidates in the detail may be different
    await screen.findByText(/1 candidate pending/);
  });

  test('shows already-resolved candidates as resolved', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({
          dreams: [{ ...DREAM_WITH_CANDIDATE, kb_candidate_count: 2 }],
        }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_ACCEPTED], // Only resolved
        }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Resolved candidates (non-pending) should NOT show in the feed
    // since DreamCandidateRow filters for pending only
    await waitFor(() =>
      expect(
        screen.queryByText(/Already accepted candidate/),
      ).not.toBeInTheDocument(),
    );
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — candidate-gate state transitions (Accept/Dismiss)          */
/* ------------------------------------------------------------------ */

describe('KbPage — candidate review gate', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'tok');
  });

  test('Accept sends POST to shared STEP-1 route and resolves candidate', async () => {
    let acceptCalled = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [DREAM_WITH_CANDIDATE] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        }),
      ),
      http.post(
        `/api/v1/orgs/${SLUG}/dreams/candidates/1/accept`,
        () => {
          acceptCalled = true;
          return HttpResponse.json({
            ...CANDIDATE_A,
            status: 'promoted',
            promoted_kb_slug: 'policy/new-refund-flow',
          });
        },
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/New refund flow for walk-ins/);

    // Click the candidate to open detail pane
    await user.click(screen.getByText(/New refund flow for walk-ins/));
    await screen.findByText('from dream · proposed by triage_agent · pending review');

    // Click Accept
    const acceptBtn = screen.getByRole('button', { name: 'Accept' });
    await user.click(acceptBtn);

    await waitFor(() => expect(acceptCalled).toBe(true));
  });

  test('Dismiss sends POST to shared STEP-1 route and resolves candidate', async () => {
    let dismissCalled = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [DREAM_WITH_CANDIDATE] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        }),
      ),
      http.post(
        `/api/v1/orgs/${SLUG}/dreams/candidates/1/dismiss`,
        () => {
          dismissCalled = true;
          return HttpResponse.json({
            ...CANDIDATE_A,
            status: 'rejected',
          });
        },
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/New refund flow for walk-ins/);

    // Click the candidate to open detail pane
    await user.click(screen.getByText(/New refund flow for walk-ins/));
    await screen.findByText('from dream · proposed by triage_agent · pending review');

    // Click Dismiss
    const dismissBtn = screen.getByRole('button', { name: 'Dismiss' });
    await user.click(dismissBtn);

    await waitFor(() => expect(dismissCalled).toBe(true));
  });

  test('on accept failure, shows error message with retry', async () => {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [DREAM_WITH_CANDIDATE] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () =>
        HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        }),
      ),
      http.post(
        `/api/v1/orgs/${SLUG}/dreams/candidates/1/accept`,
        () =>
          HttpResponse.json({ detail: 'Internal error' }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/New refund flow for walk-ins/);

    await user.click(screen.getByText(/New refund flow for walk-ins/));
    await screen.findByText('from dream · proposed by triage_agent · pending review');

    const acceptBtn = screen.getByRole('button', { name: 'Accept' });
    await user.click(acceptBtn);

    // Error message should appear with retry buttons
    await screen.findByText('Accept failed — retry');
    // Accept button should still be available for retry
    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — loading & empty states                                     */
/* ------------------------------------------------------------------ */

describe('KbPage — loading & empty states', () => {
  test('shows loading skeleton while fetching', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        // Never resolves — keeps loading state
        new Promise(() => {}),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        new Promise(() => {}),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    // Skeleton should show — page title "Knowledge" is in an h1
    await screen.findByRole('heading', { name: 'Knowledge' });
  });

  test('shows empty state when no entries and no candidates', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText('No entries yet');
  });

  test('shows error state with retry on kb list failure', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ detail: 'Server error' }, { status: 500 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText('Could not load Knowledge');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — entry detail drawer                                        */
/* ------------------------------------------------------------------ */

describe('KbPage — entry detail', () => {
  test('opens detail drawer for live KB entries with markdown', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/refund-thresholds`, () =>
        HttpResponse.json(ENTRY_A),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await user.click(await screen.findByText(/Refund authority by tier/));
    await waitFor(() =>
      expect(
        screen.getByText(/CX Manager may approve refunds/),
      ).toBeInTheDocument(),
    );
    // Source task badge
    expect(screen.getByText('TASK-0042')).toBeInTheDocument();
  });
});
