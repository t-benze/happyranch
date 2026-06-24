/**
 * KbPage tests — Knowledge surface (§4.5).
 *
 * Covers: folder filtering, candidate feed rendering, candidate-gate
 * state transitions (Accept/Dismiss), pending-count tag, error states,
 * and shared candidate state via the merged STEP-1 route.
 */
import { screen, waitFor, within } from '@testing-library/react';
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

/** Live KB entry created after accept. */
const PROMOTED_ENTRY = {
  slug: 'policy/new-refund-flow',
  title: 'New refund flow for walk-ins',
  type: 'policy',
  topic: 'policy',
  tags: ['policy'],
  body: '# New refund flow for walk-ins\n\n## Proposed\n\nAdd a new step for multi-language refund verification.',
  updated_at: '2026-06-17T09:00:00Z',
  authored_by: 'triage_agent',
  source_task: 'TASK-0099',
  related_entries: [],
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Adds the /kb/stats handler returning the given view stats. */
function stubKBStats(stats?: { slug: string; view_count: number; last_viewed_at: string }[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/kb/stats`, () =>
      HttpResponse.json({ entries: stats ?? [] }),
    ),
  );
}

/* ------------------------------------------------------------------ */
/*  Tests — folder filtering                                           */
/* ------------------------------------------------------------------ */

describe('KbPage — folder filtering', () => {
  test('filters entries by type (folder) via server param', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    await user.click(screen.getByRole('button', { name: /precedent/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
  });

  test('"All" folder clears the type filter', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    await user.click(screen.getByRole('button', { name: /precedent/ }));
    await waitFor(() => expect(serverParams).toBe('precedent'));
    await waitFor(() =>
      expect(screen.queryByText(/Spanish-speaking walk-in flow/)).not.toBeInTheDocument(),
    );
    // Click "All entries" — clears the type filter, shows cached both entries
    // (no re-fetch since the query key matches the initial cache)
    await user.click(screen.getByRole('button', { name: /All entries/i }));
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
    stubKBStats();
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

  test('shows pending-count tag from actual pending statuses, not kb_candidate_count total', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
          // 2 total candidates, but only CANDIDATE_A is pending
          kb_candidates: [CANDIDATE_A, CANDIDATE_ACCEPTED],
        }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // The pending count tag should count only pending-status candidates,
    // NOT the stored kb_candidate_count total (which would be 2).
    await screen.findByText(/1 candidate pending/);
    // CANDIDATE_ACCEPTED (promoted) should not inflate the count — verify no "2 candidates"
    expect(screen.queryByText(/2 candidates pending/)).toBeNull();
  });

  test('shows already-resolved candidates as resolved', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    stubKBStats();
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

/* ------------------------------------------------------------------ */
/*  Tests — header treatment (KB-02, THR-030)                          */
/* ------------------------------------------------------------------ */

describe('KbPage — header treatment (KB-02)', () => {
  test('renders the uppercase eyebrow with the live document count and the serif title; no plain "Knowledge" title', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        // Two entries rendered → "2 DOCUMENTS"
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    // Uppercase eyebrow reflects the live count of rendered entries.
    expect(screen.getByText('ALL ENTRIES · 2 DOCUMENTS')).toBeInTheDocument();

    // Serif title carries the font-display class and is an h1.
    const title = screen.getByRole('heading', {
      name: 'What the org has learned',
    });
    expect(title.tagName).toBe('H1');
    expect(title).toHaveClass('font-display');

    // The old plain "Knowledge" title is gone.
    expect(screen.queryByRole('heading', { name: 'Knowledge' })).toBeNull();
  });

  test('renders an amber "N candidates pending" pill when candidate data is present', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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

    const pill = await screen.findByText('1 candidate pending');
    // Amber treatment via the semantic feedback-warning token.
    expect(pill).toHaveClass('text-feedback-warning');
  });

  test('omits the candidates pill when no candidate data is available', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    expect(screen.queryByText(/candidate.? pending/)).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — loading & empty states                                     */
/* ------------------------------------------------------------------ */

describe('KbPage — loading & empty states', () => {
  test('shows loading skeleton while fetching', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    // Skeleton should show — the serif page title is in an h1 (renders
    // synchronously, before the entries fetch resolves).
    await screen.findByRole('heading', { name: 'What the org has learned' });
  });

  test('shows empty state when no entries and no candidates', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
    stubKBStats();
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
    stubKBStats();
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

/* ------------------------------------------------------------------ */
/*  Tests — usage label "viewed Nx (CLI)" (Finding 1)                  */
/* ------------------------------------------------------------------ */

describe('KbPage — viewed Nx (CLI) usage label', () => {
  test('shows viewed Nx (CLI) for entries with recorded views', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats([
      { slug: 'policy/refund-thresholds', view_count: 7, last_viewed_at: '2026-06-17T09:00:00Z' },
    ]);
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // ENTRY_A has 7 views → "viewed 7× (CLI)"
    expect(screen.getByText('viewed 7× (CLI)')).toBeInTheDocument();
    // ENTRY_B has no stats → "viewed 0× (CLI)"
    expect(screen.getByText('viewed 0× (CLI)')).toBeInTheDocument();
    // The ONLY usage copy is "viewed Nx (CLI)" — no citation/load-bearing/uncited badges
    expect(screen.queryByText(/citation/)).toBeNull();
    expect(screen.queryByText(/load-bearing/)).toBeNull();
    expect(screen.queryByText(/uncited/)).toBeNull();
    expect(screen.queryByText(/used by/)).toBeNull();
    expect(screen.queryByText(/agents/)).toBeNull();
  });

  test('viewCount is undefined (not rendered) when stats are still loading', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Don't stub stats — the query will be in loading state
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
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // When stats haven't loaded, the label is absent (viewCount=undefined)
    expect(screen.queryByText(/viewed/)).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — leading glyph on entry cards (KB-04, THR-030)              */
/* ------------------------------------------------------------------ */

describe('KbPage — leading glyph on entry cards (KB-04)', () => {
  test('each live KB entry card leads with a decorative file glyph before the slug', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    // The entry card is an anchor (Link); locate it via its slug text.
    const slugEl = screen.getByText('policy/refund-thresholds');
    const card = slugEl.closest('a');
    expect(card).not.toBeNull();

    // A leading glyph (inline svg) is present in the card header…
    const glyph = card!.querySelector('svg');
    expect(glyph).toBeTruthy();
    // …it precedes the slug text in DOM order (the card LEADS with it)…
    expect(
      glyph!.compareDocumentPosition(slugEl) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // …and it is decorative, since the title/slug already convey meaning.
    expect(glyph).toHaveAttribute('aria-hidden', 'true');
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — candidate resolution clears detail (Finding 2)             */
/* ------------------------------------------------------------------ */

describe('KbPage — candidate resolution clears detail', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'tok');
  });

  test('after Accept, the drawer shows the promoted live entry, pending count drops, and Accept/Dismiss buttons disappear', async () => {
    stubKBStats();
    let acceptSubmitted = false;
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
      // Single handler: returns pending initially, promoted after accept submitted
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () => {
        if (acceptSubmitted) {
          return HttpResponse.json({
            ...DREAM_WITH_CANDIDATE,
            kb_candidates: [{ ...CANDIDATE_A, status: 'promoted', promoted_kb_slug: 'policy/new-refund-flow' }],
          });
        }
        return HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        });
      }),
      http.post(`/api/v1/orgs/${SLUG}/dreams/candidates/1/accept`, () => {
        acceptSubmitted = true;
        return HttpResponse.json({
          ...CANDIDATE_A,
          status: 'promoted',
          promoted_kb_slug: 'policy/new-refund-flow',
        });
      }),
      // The promoted KB entry should render in the drawer
      http.get(`/api/v1/orgs/${SLUG}/kb/policy/new-refund-flow`, () =>
        HttpResponse.json(PROMOTED_ENTRY),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/New refund flow for walk-ins/);
    // Before resolve: pending count tag visible
    expect(screen.getByText(/1 candidate pending/)).toBeInTheDocument();

    // Click candidate to open detail
    await user.click(screen.getByText(/New refund flow for walk-ins/));
    await screen.findByText('from dream · proposed by triage_agent · pending review');

    // Click Accept
    const acceptBtn = screen.getByRole('button', { name: 'Accept' });
    await user.click(acceptBtn);

    // After Accept:
    // (a) Accept/Dismiss buttons disappear
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
    });
    // (b) The promoted live entry's body appears in the drawer — the markdown
    // body text is unique (not duplicated in the feed candidate row).
    await waitFor(() => {
      expect(
        screen.getByText(/Add a new step for multi-language refund verification/),
      ).toBeInTheDocument();
    });
    // (c) The pending-count tag drops (no more pending candidates)
    await waitFor(() => {
      expect(screen.queryByText(/candidate pending/)).toBeNull();
    });
  });

  test('after Dismiss, the Accept/Dismiss buttons disappear, candidate detail clears, and pending count drops', async () => {
    stubKBStats();
    let dismissSubmitted = false;
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
      // Single handler: returns pending initially, rejected after dismiss submitted
      http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0099`, () => {
        if (dismissSubmitted) {
          return HttpResponse.json({
            ...DREAM_WITH_CANDIDATE,
            kb_candidates: [{ ...CANDIDATE_A, status: 'rejected' }],
          });
        }
        return HttpResponse.json({
          ...DREAM_WITH_CANDIDATE,
          kb_candidates: [CANDIDATE_A],
        });
      }),
      http.post(`/api/v1/orgs/${SLUG}/dreams/candidates/1/dismiss`, () => {
        dismissSubmitted = true;
        return HttpResponse.json({
          ...CANDIDATE_A,
          status: 'rejected',
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/New refund flow for walk-ins/);
    // Before resolve: pending count tag visible
    expect(screen.getByText(/1 candidate pending/)).toBeInTheDocument();

    // Click candidate to open detail
    await user.click(screen.getByText(/New refund flow for walk-ins/));
    await screen.findByText('from dream · proposed by triage_agent · pending review');

    // Click Dismiss
    const dismissBtn = screen.getByRole('button', { name: 'Dismiss' });
    await user.click(dismissBtn);

    // After Dismiss:
    // (a) Accept/Dismiss buttons disappear
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull();
    });
    // (b) Pending count tag drops
    await waitFor(() => {
      expect(screen.queryByText(/candidate pending/)).toBeNull();
    });
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — debounced search (Finding 3)                               */
/* ------------------------------------------------------------------ */

describe('KbPage — debounced search', () => {
  test('typing in the search box triggers /kb/search after debounce', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
    let searchHit = false;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () =>
        HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () => {
        searchHit = true;
        return HttpResponse.json({ entries: [ENTRY_A] });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Both entries visible before search
    expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument();
    // Type a search query
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'refund');
    // After 200ms debounce + server response, search should have been called
    await waitFor(() => expect(searchHit).toBe(true), { timeout: 2000 });
  });

  test('search with empty query shows the full list (not search)', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
    let listCalled = 0;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb`, () => {
        listCalled++;
        return HttpResponse.json({ entries: [ENTRY_A, ENTRY_B] });
      }),
      http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
        HttpResponse.json({ dreams: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Type then clear
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'xyz');
    await user.clear(screen.getByPlaceholderText(/Search entries/i));
    // After clearing + debounce, the list should still show both entries
    // (search is not active when q is empty)
    await waitFor(() => expect(listCalled).toBeGreaterThanOrEqual(1));
  });

  test('search with zero results renders "No matches" even when a pending candidate exists', async () => {
    // Finding 3: when isSearching, emptiness must be decided from VISIBLE search
    // results only — not from dreamsWithCandidates which are hidden during search.
    sessionStorage.setItem('happyranch.token', 'tok');
    stubKBStats();
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
      http.get(`/api/v1/orgs/${SLUG}/kb/search`, () =>
        HttpResponse.json({ entries: [] }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);
    // Pending candidate exists (visible in feed before search)
    await screen.findByText(/New refund flow for walk-ins/);

    // Start searching
    await user.type(screen.getByPlaceholderText(/Search entries/i), 'zzz_nonexistent');

    // Should render the "No matches" empty state, NOT a blank feed
    await screen.findByText('No matches', {}, { timeout: 3000 });
    // Verify the empty body text is also present
    expect(screen.getByText('No entries match that search.')).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/*  Tests — grouped folder rail (KB-01)                                */
/* ------------------------------------------------------------------ */

/** A second precedent-type entry so the precedent folder count is 2. */
const ENTRY_C = {
  slug: 'policy/chargeback-window',
  title: 'Chargeback dispute window',
  type: 'precedent',
  topic: 'finance',
  tags: ['policy', 'finance'],
  body: '# Chargeback window\n\nDisputes must be filed within 60 days.',
  updated_at: '2026-05-18T09:00:00Z',
  authored_by: 'founder',
  source_task: 'TASK-0043',
  related_entries: [],
};

/** Stubs orgs + dreams(empty) + kb-list (type-filtered) + stats for rail tests. */
function stubRail(entries: { type: string }[]) {
  stubKBStats();
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/kb`, ({ request }) => {
      const type = new URL(request.url).searchParams.get('type');
      const filtered = type ? entries.filter((e) => e.type === type) : entries;
      return HttpResponse.json({ entries: filtered });
    }),
    http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
      HttpResponse.json({ dreams: [] }),
    ),
  );
}

describe('KbPage — grouped folder rail (KB-01)', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'tok');
  });

  test('renders a labeled Library section with an "All entries" row carrying the total count', async () => {
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    // Section label
    expect(within(rail).getByText('Library')).toBeInTheDocument();
    // "All entries" row with the total (3) count
    const allBtn = within(rail).getByRole('button', { name: /All entries/i });
    expect(allBtn).toBeInTheDocument();
    expect(within(allBtn).getByText('3')).toBeInTheDocument();
  });

  test('renders one folder per type with its correct per-folder count', async () => {
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    // precedent appears twice (ENTRY_A + ENTRY_C) → count 2
    const precedentBtn = within(rail).getByRole('button', { name: /precedent/ });
    expect(within(precedentBtn).getByText('2')).toBeInTheDocument();
    // sop appears once (ENTRY_B) → count 1
    const sopBtn = within(rail).getByRole('button', { name: /sop/ });
    expect(within(sopBtn).getByText('1')).toBeInTheDocument();
  });

  test('per-folder counts stay stable when a folder filter is active', async () => {
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    // Filter to sop — feed narrows to ENTRY_B only…
    await user.click(within(rail).getByRole('button', { name: /sop/ }));
    await waitFor(() =>
      expect(screen.queryByText(/Refund authority by tier/)).not.toBeInTheDocument(),
    );
    // …but the rail still shows the full-library counts (precedent 2, sop 1, All 3).
    expect(
      within(within(rail).getByRole('button', { name: /precedent/ })).getByText('2'),
    ).toBeInTheDocument();
    expect(
      within(within(rail).getByRole('button', { name: /All entries/i })).getByText('3'),
    ).toBeInTheDocument();
  });

  test('renders folder icons (svg) in the rail', async () => {
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    // Each folder/library row carries a leading icon glyph.
    expect(rail.querySelectorAll('svg').length).toBeGreaterThanOrEqual(3);
  });

  test('clicking a folder still filters the feed by type (behavior preserved)', async () => {
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Spanish-speaking walk-in flow/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    await user.click(within(rail).getByRole('button', { name: /precedent/ }));
    // sop-type entry drops out of the feed
    await waitFor(() =>
      expect(
        screen.queryByText(/Spanish-speaking walk-in flow/),
      ).not.toBeInTheDocument(),
    );
    // Click "All entries" → filter cleared, both visible again
    await user.click(within(rail).getByRole('button', { name: /All entries/i }));
    await waitFor(() =>
      expect(screen.getByText(/Spanish-speaking walk-in flow/)).toBeInTheDocument(),
    );
  });

  test('does NOT zero-fake the design folders that existing data cannot back', async () => {
    // The design (a-knowledge) shows Engineering→review/qa/build and
    // Org→protocols/from-dreams. The kb-list payload has no origin/category
    // field to back those, so the rail must honestly omit them, not render
    // them with a faked 0/placeholder count.
    stubRail([ENTRY_A, ENTRY_B, ENTRY_C]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/kb` });
    await screen.findByText(/Refund authority by tier/);

    const rail = screen.getByRole('complementary', { name: /KB folders/i });
    expect(within(rail).queryByText(/from dreams?/i)).toBeNull();
    expect(within(rail).queryByRole('button', { name: /^review$/ })).toBeNull();
    expect(within(rail).queryByRole('button', { name: /^build$/ })).toBeNull();
    expect(within(rail).queryByRole('button', { name: /^protocols$/ })).toBeNull();
  });
});
