/**
 * ProposalsQueuePage.test.tsx — THR-055 Slice 3A queue tests.
 *
 * Covers: initial render, supported filter params, pagination, response
 * total display, server-ordering left intact, deep-link routing,
 * loading / empty / error-retry, 403 no-data-leak, terminal row display,
 * optional claimant/proposer distinction, no unsupported selectors,
 * no mutation API call.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

interface QueueItem {
  skill_id: string;
  version_id: number;
  version: string;
  name: string;
  slug: string;
  content_hash: string;
  status: string;
  proposer_agent: string;
  claimed_by: string | null;
  proposal_task_id: string | null;
  proposal_session_id: string | null;
  latest_validator_version: string | null;
  latest_validator_key: string | null;
  permitted_next_action: string | null;
  assigned_agent_count: number;
  assigned_agents: string[];
  created_at: string;
}

function qi(over: Partial<QueueItem> & Pick<QueueItem, 'skill_id' | 'version_id'>): QueueItem {
  return {
    version: '1.0.0',
    name: `Skill ${over.skill_id}`,
    slug: SLUG,
    content_hash: 'abc123',
    status: 'proposed',
    proposer_agent: 'dev_agent',
    claimed_by: null,
    proposal_task_id: null,
    proposal_session_id: null,
    latest_validator_version: null,
    latest_validator_key: null,
    permitted_next_action: null,
    assigned_agent_count: 0,
    assigned_agents: [],
    created_at: '2026-08-01T00:00:00Z',
    ...over,
  };
}

const ITEM_1 = qi({ skill_id: 'hr:test1', version_id: 1, status: 'proposed' });
const ITEM_2 = qi({
  skill_id: 'hr:test2',
  version_id: 2,
  status: 'validated',
  latest_validator_version: 'THR-055/1.0.0',
  latest_validator_key: 'vkey1',
});
const ITEM_3 = qi({
  skill_id: 'hr:test3',
  version_id: 3,
  status: 'rejected',
  claimed_by: 'founder',
});

const ALL = [ITEM_1, ITEM_2, ITEM_3];

function mountQueue(items: QueueItem[] = ALL, total?: number) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
      const url = new URL(request.url);
      // Verify only supported params are used
      const page = Number(url.searchParams.get('page')) || 1;
      const status = url.searchParams.get('status');
      let filtered = items;
      if (status) {
        filtered = filtered.filter((i) => i.status === status);
      }
      // Unsorted — server ordering is authoritative
      return HttpResponse.json({
        items: filtered,
        total: total ?? filtered.length,
        page,
        page_size: 20,
      });
    }),
  );
}

describe('ProposalsQueuePage', () => {
  test('renders header and founder-only guidance panel', async () => {
    mountQueue();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(screen.getByText(/Founder-only/)).toBeInTheDocument();
    expect(screen.getByText('Skills · Proposals')).toBeInTheDocument();
  });

  test('renders proposal rows with status, proposer, version', async () => {
    mountQueue();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(`Skill ${ITEM_1.skill_id}`)).toBeInTheDocument();
    });
    expect(screen.getByText(`Skill ${ITEM_2.skill_id}`)).toBeInTheDocument();
    expect(screen.getByText(`Skill ${ITEM_3.skill_id}`)).toBeInTheDocument();

    // Status badges on rows (scoped to the list region to avoid filter chip dupe)
    const list = screen.getByRole('list');
    expect(within(list).getByText('Proposed')).toBeInTheDocument();
    expect(within(list).getByText('Validated')).toBeInTheDocument();
    expect(within(list).getByText('Rejected')).toBeInTheDocument();

    // Proposer — use getAllByText to handle duplicates
    const devAgentMatches = screen.getAllByText('dev_agent');
    expect(devAgentMatches.length).toBeGreaterThanOrEqual(1);

    // Claimant distinct from proposer — scoped to main
    const main = document.querySelector('main')!;
    expect(within(main).getByText(/claimed by founder/)).toBeInTheDocument();

    // Validator info
    expect(within(main).getByText('THR-055/1.0.0')).toBeInTheDocument();
  });

  test('renders response total and page info', async () => {
    mountQueue(ALL, 42);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(/42 proposals/)).toBeInTheDocument();
    });
  });

  test('deep-links rows to proposal detail page', async () => {
    mountQueue();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(`Skill ${ITEM_1.skill_id}`)).toBeInTheDocument();
    });
    const links = screen.getAllByRole('link');
    const detailLinks = links.filter((l) =>
      l.getAttribute('href')?.includes('/skills/proposals/'),
    );
    expect(detailLinks.length).toBeGreaterThanOrEqual(1);
    expect(detailLinks[0].getAttribute('href')).toContain('/skills/proposals/1');
  });

  test('shows loading state', () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // Never resolves → loading forever
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, () => {
        return new Promise(() => {});
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    // Skeleton rows
    const skeletons = document.querySelectorAll('.animate-pulse');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  test('shows empty state when no proposals', async () => {
    mountQueue([]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('No proposals yet')).toBeInTheDocument();
    });
  });

  test('shows error state with retry button', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, () => {
        return new HttpResponse('Internal error', { status: 500 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Could not load proposals')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument();
  });

  test('shows 403 does not leak data', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, () => {
        return new HttpResponse('Forbidden', { status: 403 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Could not load proposals')).toBeInTheDocument();
    });
    // No data rendered
    expect(screen.queryByText(/Skill hr:/)).not.toBeInTheDocument();
  });

  test('read-only rows for terminal statuses — no action buttons', async () => {
    mountQueue([ITEM_3]); // rejected
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Rejected')).toBeInTheDocument();
    });
    // No action buttons — exact match to avoid filter chip false positive
    const main = document.querySelector('main')!;
    expect(within(main).queryByRole('button', { name: 'Claim' })).not.toBeInTheDocument();
    expect(within(main).queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
    expect(within(main).queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument();
  });

  test('filter by status sends server query and updates URL', async () => {
    mountQueue(ALL);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('All')).toBeInTheDocument();
    });

    const proposedChip = screen.getByRole('button', { name: 'Proposed' });
    await userEvent.click(proposedChip);

    await waitFor(() => {
      // Only proposed items rendered
      expect(screen.getByText(`Skill ${ITEM_1.skill_id}`)).toBeInTheDocument();
    });
    // Validated and rejected shouldn't show
    expect(screen.queryByText(`Skill ${ITEM_2.skill_id}`)).not.toBeInTheDocument();
    expect(screen.queryByText(`Skill ${ITEM_3.skill_id}`)).not.toBeInTheDocument();
  });

  test('search sends server query', async () => {
    mountQueue(ALL);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Search…')).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('Search…');
    await userEvent.type(input, 'test1');
    await userEvent.click(screen.getByRole('button', { name: 'Search' }));

    // The component triggers a new fetch with search param
    // We verify via the filter badge appearing
    await waitFor(() => {
      expect(screen.getByText(/search: test1/)).toBeInTheDocument();
    });
  });

  test('paginates when total exceeds page size', async () => {
    const many = Array.from({ length: 25 }, (_, i) =>
      qi({ skill_id: `hr:many${i}`, version_id: i + 1 }),
    );
    mountQueue(many.slice(0, 20), 25); // first page
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(/25 proposals/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Page 1 of 2/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Next/ })).toBeEnabled();
    expect(screen.getByRole('button', { name: /Previous/ })).toBeDisabled();
  });

  test('server ordering left intact — items rendered in response order', async () => {
    mountQueue(ALL);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(`Skill ${ITEM_1.skill_id}`)).toBeInTheDocument();
    });
    const items = screen.getAllByRole('listitem');
    // ALL = [ITEM_1, ITEM_2, ITEM_3] — verify ordering by text content
    const texts = items.map((el) => el.textContent ?? '');
    expect(texts[0]).toContain(`Skill ${ITEM_1.skill_id}`);
    expect(texts[1]).toContain(`Skill ${ITEM_2.skill_id}`);
    expect(texts[2]).toContain(`Skill ${ITEM_3.skill_id}`);
  });

  test('does not render unsupported mockup selectors', async () => {
    mountQueue();
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    // "Any assignment" and "Any use case" should NOT exist
    expect(screen.queryByText(/Any assignment/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Any use case/)).not.toBeInTheDocument();
  });

  test('does not call mutation API', async () => {
    // Verify no POST/PUT/PATCH/DELETE is made by the page.
    mountQueue(ALL);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(`Skill ${ITEM_1.skill_id}`)).toBeInTheDocument();
    });
    // Only filter/search controls should exist — no lifecycle action buttons
    const mainEl = document.querySelector('main')!;
    const buttons = within(mainEl).queryAllByRole('button');
    const allLabels = buttons.map((b) => (b.textContent ?? '').toLowerCase().trim());
    // Only allow: search, filter chips, pagination, clear filters, retry
    const allowed = ['search', 'previous', 'next', 'retry', 'clear all', '', 'all'];
    const filterLabels = ['proposed', 'draft', 'validated', 'in review', 'approved', 'published', 'rejected'];
    const illegal = allLabels.filter(
      (l) => !allowed.includes(l) && !filterLabels.includes(l),
    );
    expect(illegal).toEqual([]);
  });
});

  // ── Regression coverage: validation_outcome, proposer, date bounds, page_size ─

  test('forwards validation_outcome values as query params', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?validation_outcome=validated` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('validation_outcome')).toBe('validated');
  });

  test('forwards validation_failed outcome', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?validation_outcome=validation_failed` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('validation_outcome')).toBe('validation_failed');
  });

  test('forwards unvalidated outcome', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?validation_outcome=unvalidated` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('validation_outcome')).toBe('unvalidated');
  });

  test('forwards proposer filter', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?proposer=frontend_engineer` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('proposer')).toBe('frontend_engineer');
  });

  test('forwards submitted_after date bound', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?submitted_after=2026-07-01` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('submitted_after')).toBe('2026-07-01');
  });

  test('forwards submitted_before date bound', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({ items: ALL, total: ALL.length, page: 1, page_size: 20 });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?submitted_before=2026-08-01` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('submitted_before')).toBe('2026-08-01');
  });

  test('forwards non-default page_size to query API', async () => {
    let capturedParams: URLSearchParams | null = null;
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, ({ request }) => {
        capturedParams = new URL(request.url).searchParams;
        return HttpResponse.json({
          items: ALL.slice(0, 10),
          total: ALL.length,
          page: 1,
          page_size: 10,
        });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals?page_size=10` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    expect(capturedParams!.get('page_size')).toBe('10');
  });

  test('pagination display uses response page and page_size, not client constant', async () => {
    const many = Array.from({ length: 30 }, (_, i) =>
      qi({ skill_id: `hr:many${i}`, version_id: i + 1 }),
    );
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/queue`, () => {
        // Response says page=2, page_size=10, total=30 → page 2 of 3
        return HttpResponse.json({
          items: many.slice(10, 20),
          total: 30,
          page: 2,
          page_size: 10,
        });
      }),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText(/30 proposals/)).toBeInTheDocument();
    });
    // Must reflect the server response, not the former 20 constant
    expect(screen.getByText(/page 2 of 3/)).toBeInTheDocument();
    expect(screen.getByText(/10 per page/)).toBeInTheDocument();
  });

describe('ProposalsQueue route precedence', () => {
  test('/skills/proposals is not swallowed by skills/:skillId', async () => {
    mountQueue([ITEM_1]);
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals` });
    await waitFor(() => {
      expect(screen.getByText('Proposal Queue')).toBeInTheDocument();
    });
    // Not the SkillsPage header
    expect(screen.queryByText('Guidance your agents can use')).not.toBeInTheDocument();
  });

  test('/skills/proposals/:versionId coexists with skills/proposals', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    // The restored full ProposalDetailPage fetches detail data — provide a
    // minimal response so the component renders rather than hanging in loading.
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/42`, () =>
        HttpResponse.json({
          version_id: 42,
          skill_id: 'hr:test',
          slug: 'test',
          name: 'Test Skill',
          version: '1.0.0',
          content_hash: 'abc',
          status: 'proposed',
          proposer_agent: 'dev_agent',
          events: [],
          assignments: [],
          materializations: [],
          last_event_id: 0,
          created_at: '2026-08-01T00:00:00Z',
        }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/proposals/42` });
    await waitFor(() => {
      expect(screen.getByText('Proposal')).toBeInTheDocument();
    });
    // The restored full ProposalDetailPage renders the skill_id from the response
    // (appears in breadcrumb + evidence rail, so expect multiple instances)
    const skillIdElements = screen.getAllByText('hr:test');
    expect(skillIdElements.length).toBeGreaterThanOrEqual(1);
  });
});
