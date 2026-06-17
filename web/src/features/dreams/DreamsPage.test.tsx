import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Mock the dreams hooks so the page renders deterministically
vi.mock('@/hooks/dreams', () => ({
  useDreamsList: vi.fn(),
  useDream: vi.fn(),
  useAcceptCandidate: vi.fn(),
  useDismissCandidate: vi.fn(),
  useDreamsRoutes: () => ({
    inbox: () => '/orgs/test-org/dreams',
    detail: (id: string) => `/orgs/test-org/dreams/${id}`,
    inboxForOrg: () => '/orgs/test-org/dreams',
  }),
}));

function renderPage(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.spyOn(qc, 'invalidateQueries');
  const rendered = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/test-org/dreams']}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...rendered, qc };
}

// Mock useParams so DreamsPage reads the slug from context
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ slug: 'test-org' }),
  };
});

import { useDreamsList, useDream, useAcceptCandidate, useDismissCandidate } from '@/hooks/dreams';
import { DreamsPage } from './DreamsPage';

const mockDreamsList = vi.mocked(useDreamsList);
const mockDream = vi.mocked(useDream);
const mockAcceptCandidate = vi.mocked(useAcceptCandidate);
const mockDismissCandidate = vi.mocked(useDismissCandidate);

function loaded<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

function loading() {
  return { data: undefined, isLoading: true, isError: false, error: null };
}

function errored() {
  return { data: undefined, isLoading: false, isError: true, error: new Error('fail') };
}

const QUIET_DREAM = {
  dream_id: 'DREAM-0012',
  agent_name: 'engineering_manager',
  local_date: '2026-06-18',
  scheduled_for: '2026-06-18T03:00:00Z',
  window_start: '2026-06-18T02:55:00Z',
  window_end: '2026-06-18T03:10:00Z',
  started_at: '2026-06-18T03:00:05Z',
  ended_at: '2026-06-18T03:03:42Z',
  status: 'completed',
  summary: 'Routine nightly reflection. No issues found.',
  transcript_path: null,
  new_learnings_count: 2,
  kb_candidate_count: 0,
  founder_thread_id: null,
  error: null,
};

const DREAM_WITH_CANDIDATES = {
  dream_id: 'DREAM-0011',
  agent_name: 'product_lead',
  local_date: '2026-06-18',
  scheduled_for: '2026-06-18T03:00:00Z',
  window_start: null,
  window_end: '2026-06-18T03:10:00Z',
  started_at: '2026-06-18T03:00:02Z',
  ended_at: '2026-06-18T03:05:11Z',
  status: 'completed',
  summary: 'Identified a recurring pattern.',
  transcript_path: null,
  new_learnings_count: 1,
  kb_candidate_count: 2,
  founder_thread_id: 'THR-010',
  error: null,
};

const FAILED_DREAM = {
  dream_id: 'DREAM-0009',
  agent_name: 'dev_agent',
  local_date: '2026-06-17',
  scheduled_for: '2026-06-17T03:00:00Z',
  window_start: null,
  window_end: '2026-06-17T03:10:00Z',
  started_at: '2026-06-17T03:00:07Z',
  ended_at: null,
  status: 'failed',
  summary: null,
  transcript_path: null,
  new_learnings_count: 0,
  kb_candidate_count: 0,
  founder_thread_id: null,
  error: 'Executor API returned 503',
};

const MISSED_DREAM = {
  dream_id: 'DREAM-0008',
  agent_name: 'engineering_manager',
  local_date: '2026-06-17',
  scheduled_for: '2026-06-17T03:00:00Z',
  window_start: null,
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
};

const DREAM_DETAIL_RESPONSE = {
  ...DREAM_WITH_CANDIDATES,
  transcript: '## Reflection\n\nIdentified a recurring pattern.',
  kb_candidates: [
    {
      id: 1,
      dream_id: 'DREAM-0011',
      agent_name: 'product_lead',
      slug: 'routing/spanish-after-hours',
      title: 'Spanish after-hours routing',
      topic: 'routing',
      rationale: 'Recurring pattern observed.',
      body_markdown: '# Spanish after-hours',
      status: 'pending',
      promoted_kb_slug: null,
      created_at: '2026-06-18T03:05:11Z',
      updated_at: '2026-06-18T03:05:11Z',
    },
    {
      id: 2,
      dream_id: 'DREAM-0011',
      agent_name: 'product_lead',
      slug: 'policy/deposit',
      title: 'Deposit policy',
      topic: 'finance',
      rationale: 'Standardizing.',
      body_markdown: '# Deposit policy',
      status: 'rejected',
      promoted_kb_slug: null,
      created_at: '2026-06-18T03:05:11Z',
      updated_at: '2026-06-18T03:06:00Z',
    },
  ],
};

function mutationLike<T>() {
  return {
    mutateAsync: vi.fn().mockResolvedValue({} as T),
    isPending: false,
  };
}

describe('DreamsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockAcceptCandidate.mockReturnValue(mutationLike());
    mockDismissCandidate.mockReturnValue(mutationLike());
  });

  /* ---------------------------------------------------------------- */
  /*  List rendering                                                   */
  /* ---------------------------------------------------------------- */

  it('renders the page header', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [] }));
    renderPage(<DreamsPage />);
    expect(screen.getByText('Dreams')).toBeDefined();
    expect(screen.getByText('Nightly agent reflections and knowledge proposals')).toBeDefined();
  });

  it('shows loading skeletons when data is loading', () => {
    mockDreamsList.mockReturnValue(loading());
    renderPage(<DreamsPage />);
    // Skeleton has animate-pulse class
    expect(document.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('shows empty state when no dreams', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [] }));
    renderPage(<DreamsPage />);
    expect(screen.getByText('No dreams yet')).toBeDefined();
    expect(
      screen.getByText('Dreams run on the schedule configured in Settings. First reflection will appear here.'),
    ).toBeDefined();
  });

  it('shows error state when query errors', () => {
    mockDreamsList.mockReturnValue(errored());
    renderPage(<DreamsPage />);
    expect(screen.getByText("Couldn't load dreams")).toBeDefined();
  });

  it('renders dream cards with status badges', () => {
    mockDreamsList.mockReturnValue(
      loaded({ dreams: [QUIET_DREAM, DREAM_WITH_CANDIDATES, FAILED_DREAM, MISSED_DREAM] }),
    );
    renderPage(<DreamsPage />);

    expect(screen.getByText('DREAM-0012')).toBeDefined();
    expect(screen.getByText('DREAM-0011')).toBeDefined();
    expect(screen.getByText('DREAM-0009')).toBeDefined();
    expect(screen.getByText('DREAM-0008')).toBeDefined();

    // Multiple completed cards; check they all appear
    const completedBadges = screen.getAllByText('Completed');
    expect(completedBadges.length).toBe(2);
    expect(screen.getByText('Failed')).toBeDefined();
    expect(screen.getByText('Missed')).toBeDefined();
  });

  /* ---------------------------------------------------------------- */
  /*  Quiet-dream state (§2.5.5)                                       */
  /* ---------------------------------------------------------------- */

  it('shows quiet-dream state for completed dream with no candidates but learnings', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [QUIET_DREAM] }));
    renderPage(<DreamsPage />);
    expect(
      screen.getByText('Quiet dream — nothing escalated · private learning saved'),
    ).toBeDefined();
    // Check it's a quiet dream, not an empty/error state
    expect(screen.queryByText("Couldn't load dreams")).toBeNull();
    expect(screen.queryByText('No dreams yet')).toBeNull();
  });

  /* ---------------------------------------------------------------- */
  /*  Card routing: click dream-card → detail                          */
  /* ---------------------------------------------------------------- */

  it('opens detail drawer when a dream card is clicked, toggles on second click', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(loaded(DREAM_DETAIL_RESPONSE));

    renderPage(<DreamsPage />);

    // Click the dream card
    const card = screen.getByText('DREAM-0011').closest('button');
    expect(card).not.toBeNull();
    fireEvent.click(card!);

    // Detail drawer should show agent + date
    expect(screen.getByText('product_lead · 2026-06-18')).toBeDefined();
    // Candidate should be visible
    expect(screen.getByText('Spanish after-hours routing')).toBeDefined();
  });

  it('closes detail drawer on second click of same card', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(loaded(DREAM_DETAIL_RESPONSE));

    renderPage(<DreamsPage />);

    const card = screen.getByText('DREAM-0011').closest('button')!;
    fireEvent.click(card);
    expect(screen.getByText('product_lead · 2026-06-18')).toBeDefined();

    // Click again to close
    fireEvent.click(card);
    // Title should not be visible anymore (drawer closed)
    expect(screen.queryByText('product_lead · 2026-06-18')).toBeNull();
  });

  /* ---------------------------------------------------------------- */
  /*  Reflection thread link                                           */
  /* ---------------------------------------------------------------- */

  it('renders reflection thread link when founder_thread_id exists', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    renderPage(<DreamsPage />);
    const link = screen.getByRole('link', { name: /Open reflection thread/ });
    expect(link).toBeDefined();
    expect(link).toHaveAttribute('href', '/orgs/test-org/threads/THR-010');
  });

  it('shows "no reflection thread" when founder_thread_id is null', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [QUIET_DREAM] }));
    renderPage(<DreamsPage />);
    expect(screen.getByText('No reflection thread opened')).toBeDefined();
  });

  /* ---------------------------------------------------------------- */
  /*  Failed dream error display                                       */
  /* ---------------------------------------------------------------- */

  it('shows error text for failed dreams', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [FAILED_DREAM] }));
    renderPage(<DreamsPage />);
    expect(screen.getByText('Executor API returned 503')).toBeDefined();
  });

  /* ---------------------------------------------------------------- */
  /*  Candidate review gate in detail drawer                           */
  /* ---------------------------------------------------------------- */

  it('shows pending candidate label and accept/dismiss buttons', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(loaded(DREAM_DETAIL_RESPONSE));

    renderPage(<DreamsPage />);

    const card = screen.getByText('DREAM-0011').closest('button')!;
    fireEvent.click(card);

    // Honest provenance label for pending candidate
    expect(
      screen.getByText('from dream · proposed by product_lead · pending review'),
    ).toBeDefined();

    // Accept and Dismiss buttons
    expect(screen.getByRole('button', { name: 'Accept' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDefined();
  });

  it('shows resolved state for rejected candidates (no accept/dismiss)', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(loaded(DREAM_DETAIL_RESPONSE));

    renderPage(<DreamsPage />);

    const card = screen.getByText('DREAM-0011').closest('button')!;
    fireEvent.click(card);

    // Rejected candidate label
    expect(
      screen.getByText('from dream · proposed by product_lead · dismissed'),
    ).toBeDefined();

    // "Dismissed" indicator
    expect(screen.getByText('Dismissed')).toBeDefined();

    // Accept button should only appear once (for the pending candidate)
    const acceptButtons = screen.getAllByRole('button', { name: 'Accept' });
    expect(acceptButtons).toHaveLength(1);
  });

  /* ---------------------------------------------------------------- */
  /*  A4 dream marker — crescent moon badge                            */
  /* ---------------------------------------------------------------- */

  it('renders crescent moon badge SVG on dream cards', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [QUIET_DREAM] }));
    renderPage(<DreamsPage />);
    // The SVG is rendered as aria-hidden
    const svgs = document.querySelectorAll('svg[aria-hidden="true"]');
    expect(svgs.length).toBeGreaterThan(0);
  });

  it('renders italic quote with accent left-border for summary', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [QUIET_DREAM] }));
    renderPage(<DreamsPage />);
    const quote = screen.getByText('Routine nightly reflection. No issues found.');
    expect(quote).toBeDefined();
    // Should have italic class
    expect(quote.classList.contains('italic')).toBe(true);
  });

  /* ---------------------------------------------------------------- */
  /*  FINDING 2 — Dreams list error retry                              */
  /* ---------------------------------------------------------------- */

  it('renders Retry button on Dreams list error and invokes query invalidation', () => {
    mockDreamsList.mockReturnValue(errored());
    const { qc } = renderPage(<DreamsPage />);

    // Error text should be visible
    expect(screen.getByText("Couldn't load dreams")).toBeDefined();

    // Retry button should render
    const retryButton = screen.getByRole('button', { name: 'Retry' });
    expect(retryButton).toBeDefined();

    // Clicking Retry should invalidate the dreams-list query
    fireEvent.click(retryButton);
    expect(qc.invalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['dreams-list', 'test-org'] }),
    );
  });

  /* ---------------------------------------------------------------- */
  /*  FINDING 3 — Detail skeleton loading + error retry                */
  /* ---------------------------------------------------------------- */

  it('renders skeleton in detail drawer when dream detail is loading', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(loading());

    renderPage(<DreamsPage />);

    // Click to open detail drawer
    const card = screen.getByText('DREAM-0011').closest('button')!;
    fireEvent.click(card);

    // Detail drawer should show skeleton (animate-pulse)
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders Retry button on dream detail error and invokes query invalidation', () => {
    mockDreamsList.mockReturnValue(loaded({ dreams: [DREAM_WITH_CANDIDATES] }));
    mockDream.mockReturnValue(errored());

    const { qc } = renderPage(<DreamsPage />);

    // Click to open detail drawer
    const card = screen.getByText('DREAM-0011').closest('button')!;
    fireEvent.click(card);

    // Error text should be visible in drawer
    expect(screen.getByText("Couldn't load dreams")).toBeDefined();

    // Retry button should render
    const retryButton = screen.getByRole('button', { name: 'Retry' });
    expect(retryButton).toBeDefined();

    // Clicking Retry should invalidate the dream detail query
    fireEvent.click(retryButton);
    expect(qc.invalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['dream', 'test-org', 'DREAM-0011'] }),
    );
  });
});
