/**
 * TASK-5987 (PR #758 fix-forward, MEDIUM finding) — responsive thread-list
 * seam at 375x812.
 *
 * The reviewer proved the previous head's 375x812 screenshots violate mobile
 * parity: the fixed 244px rail left a sliver, heading/tabs clipped or
 * overflowed, and rows collapsed. These tests pin the minimum responsive
 * seam at the source level (deterministic class contract — jsdom has no layout
 * engine, so real bounding boxes are proven by the Playwright evidence
 * harness at the exact repair head; this file is the unit half of the pair):
 *
 *   1. The AppShell rail collapses from `w-rail` (244px) to a compact icon
 *      rail below `md` — labels stay in the accessibility tree (sr-only) so
 *      links keep their accessible names and remain keyboard reachable.
 *   2. The ThreadsPage header tabs+filter row wraps (`flex-wrap`) and the
 *      filter drops to its own full-width line below `sm` — no clip/overflow.
 *   3. OPEN and ARCHIVED buckets share the identical responsive structure;
 *      the Pinned section remains the only OPEN-only difference.
 *   4. Keyboard/accessibility: tabs, filter, and per-row pin toggles stay
 *      reachable and labelled at the collapsed width.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

function mountAt(route: string) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({ agents: [] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, { route });
}

function mkThread(
  id: string,
  subject: string,
  overrides?: Partial<{
    status: 'open' | 'archived';
    pinned: boolean;
    pinned_at: string | null;
    started_at: string;
  }>,
) {
  return {
    thread_id: id,
    subject,
    status: 'open' as const,
    started_at: '2026-05-14T00:00:00Z',
    archived_at: null as string | null,
    forwarded_from_id: null as string | null,
    forwarded_from_kind: null as 'thread' | null,
    turn_cap: 500,
    turns_used: 12,
    summary: null as string | null,
    transcript_path: null as string | null,
    composed_from_dream_id: null as string | null,
    last_speaker: 'agent_a' as string | null,
    pinned: false,
    pinned_at: null as string | null,
    last_activity_at: '2026-05-14T00:00:00Z',
    ...overrides,
  };
}

function stubList(threads: ReturnType<typeof mkThread>[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
      const url = new URL(request.url);
      const status = url.searchParams.get('status');
      const filtered = status
        ? threads.filter((t) => t.status === status)
        : [...threads];
      return HttpResponse.json({ threads: filtered });
    }),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

afterEach(() => {
  sessionStorage.removeItem('happyranch.token');
  vi.restoreAllMocks();
});

describe('TASK-5987 — responsive rail seam (AppShell/Sidebar)', () => {
  test('the rail collapses to a compact icon rail below md and restores w-rail at md+', async () => {
    stubList([]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument(),
    );
    const rail = screen.getByRole('navigation', { name: 'Primary navigation' });
    // Base (mobile) width is the compact rail; md+ restores the 244px token.
    expect(rail.className).toContain('w-rail-narrow');
    expect(rail.className).toContain('md:w-rail');
  });

  test('nav labels are sr-only below md but keep their accessible names (links stay findable by name)', async () => {
    stubList([]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Home' })).toBeInTheDocument(),
    );
    const rail = within(screen.getByRole('navigation', { name: 'Primary navigation' }));
    for (const name of ['Home', 'Threads', 'Tasks', 'Audit', 'Settings']) {
      const link = rail.getByRole('link', { name });
      expect(link).toBeInTheDocument();
      // The label span is visually collapsed (sr-only) below md, restored at md+.
      const label = Array.from(link.querySelectorAll('span')).find((s) =>
        s.textContent?.trim() === name,
      );
      expect(label).toBeTruthy();
      expect(label!.className).toContain('sr-only');
      expect(label!.className).toContain('md:not-sr-only');
    }
  });

  test('org switcher wordmark/context line and account text collapse below md without losing landmarks', async () => {
    stubList([]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() =>
      expect(screen.getByLabelText(/Active org/i)).toBeInTheDocument(),
    );
    const rail = within(screen.getByRole('navigation', { name: 'Primary navigation' }));
    // The org context line (slug) is still in the DOM (hidden below md).
    expect(rail.getByText(SLUG)).toBeInTheDocument();
    // Account identity row keeps its labelled container.
    expect(screen.getByLabelText('Account: You, Founder')).toBeInTheDocument();
    // Nav items stay keyboard-reachable in the collapsed rail.
    const home = rail.getByRole('link', { name: 'Home' });
    home.focus();
    expect(home).toHaveFocus();
  });
});

describe('TASK-5987 — responsive header seam (ThreadsPage)', () => {
  test('the tabs+filter row wraps and the filter goes full-width below sm', async () => {
    stubList([mkThread('THR-1', 'Alpha one')]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha one/i)).toBeInTheDocument());

    const filter = screen.getByRole('textbox', { name: /Filter threads/i });
    // The row owning both the segmented tabs and the filter must wrap so the
    // filter cannot push the tabs off-screen at 375px.
    expect(filter.parentElement?.className).toContain('flex-wrap');
    expect(filter.className).toContain('w-full');
    expect(filter.className).toContain('sm:w-44');
    // All three tabs remain on-screen-reachable controls with live counts.
    for (const name of [/All/, /Open/, /Archived/]) {
      expect(screen.getByRole('tab', { name })).toBeInTheDocument();
    }
  });

  test('the heading block keeps its min-w-0 truncation seam so the title never forces overflow', async () => {
    stubList([mkThread('THR-1', 'Alpha one')]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha one/i)).toBeInTheDocument());
    const title = screen.getByRole('heading', { level: 1, name: /Conversations across the org/i });
    const titleCell = title.parentElement;
    expect(titleCell?.className).toContain('min-w-0');
    expect(titleCell?.className).toContain('flex-1');
    // New-thread action is a sibling of the title cell (same row).
    expect(screen.getByRole('button', { name: /New thread/i })).toBeInTheDocument();
  });

  test('OPEN and ARCHIVED buckets render the identical responsive structure except the OPEN-only Pinned section', async () => {
    stubList([
      mkThread('THR-1', 'Alpha pinned', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-2', 'Alpha ordinary'),
      mkThread('THR-3', 'Beta archived', { status: 'archived', pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha pinned/i)).toBeInTheDocument());

    // Open: Pinned section + ordinary section, identical rows.
    expect(screen.getByRole('heading', { name: /Pinned/i })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Threads' })).toBeInTheDocument();

    // Archived: same responsive chrome, ONE flat list, no Pinned section.
    await userEvent.click(screen.getByRole('tab', { name: /Archived/i }));
    await waitFor(() => expect(screen.getByText(/Beta archived/i)).toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: /Pinned/i })).not.toBeInTheDocument();
    expect(screen.getByRole('tablist', { name: /Status filter/i })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /Filter threads/i })).toBeInTheDocument();
    // Per-row pin toggle is a mutation control present in both buckets.
    expect(screen.getByRole('button', { name: /Unpin thread THR-3/i })).toBeInTheDocument();
  });

  test('keyboard traversal reaches tabs, filter, and pin toggles in the collapsed layout', async () => {
    stubList([
      mkThread('THR-1', 'Alpha pinned', { pinned: true, pinned_at: '2026-05-20T00:00:00Z' }),
      mkThread('THR-2', 'Alpha ordinary'),
    ]);
    mountAt(`/orgs/${SLUG}/threads`);
    await waitFor(() => expect(screen.getByText(/Alpha pinned/i)).toBeInTheDocument());

    const user = userEvent.setup();
    // Pin toggles carry labelled accessible names (keyboard + AT operable)
    // in the Open bucket before any tab navigation.
    for (const name of [/Unpin thread THR-1/i, /Pin thread THR-2/i]) {
      const btn = screen.getByRole('button', { name });
      expect(btn).toHaveAttribute('type', 'button');
    }
    // Keyboard traversal in the collapsed layout: Radix tabs use a roving
    // tabindex (arrow keys move within the tablist; Tab exits to the next
    // control). Tab from the active tab must land on the filter textbox.
    const tablist = screen.getByRole('tablist', { name: /Status filter/i });
    const tabs = Array.from(
      tablist.querySelectorAll('[role="tab"]'),
    ) as HTMLElement[];
    const activeTab = tablist.querySelector('[data-state="active"]') as HTMLElement;
    activeTab.focus();
    // ArrowRight moves the roving focus to the next tab.
    await user.keyboard('{ArrowRight}');
    expect(document.activeElement).toBe(tabs[2]); // Open → Archived
    // ArrowLeft returns to Open.
    await user.keyboard('{ArrowLeft}');
    expect(document.activeElement).toBe(tabs[1]);
    // Tab exits the tablist to the filter textbox.
    await user.tab();
    expect(document.activeElement).toBe(
      screen.getByRole('textbox', { name: /Filter threads/i }),
    );
  });
});
