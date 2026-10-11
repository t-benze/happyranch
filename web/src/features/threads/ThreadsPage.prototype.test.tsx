import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import * as ReactModule from 'react';
import * as JSXModule from 'react/jsx-runtime';
import * as JSXDevModule from 'react/jsx-dev-runtime';
import * as QueryModule from '@tanstack/react-query';
import * as RouterModule from 'react-router-dom';
import type { QueryClient } from '@tanstack/react-query';

const allOrder = [
  'THR-259', 'THR-251', 'THR-258', 'THR-250', 'THR-10', 'THR-257', 'THR-249', 'THR-2',
  'THR-256', 'THR-248', 'THR-255', 'THR-247', 'THR-254', 'THR-246', 'THR-253', 'THR-245',
  'THR-252', 'THR-244', 'THR-243', 'THR-242', 'THR-241', 'THR-240', 'THR-239', 'THR-238',
  'THR-237', 'THR-236', 'THR-235', 'THR-234', 'THR-233', 'THR-232', 'THR-231', 'THR-230',
  'THR-229', 'THR-228', 'THR-227', 'THR-226', 'THR-225', 'THR-224', 'THR-223', 'THR-222',
  'THR-221', 'THR-220', 'THR-219', 'THR-218', 'THR-217', 'THR-216', 'THR-215', 'THR-214',
  'THR-213', 'THR-212', 'THR-211', 'THR-210', 'THR-209', 'THR-208', 'THR-207', 'THR-206',
  'THR-205', 'THR-204', 'THR-203', 'THR-202', 'THR-201',
];
const ordinaryOpen = [
  'THR-251', 'THR-250', 'THR-249', 'THR-248', 'THR-247', 'THR-246', 'THR-245', 'THR-244',
  'THR-243', 'THR-242', 'THR-241', 'THR-240', 'THR-239', 'THR-238', 'THR-237', 'THR-236',
  'THR-235', 'THR-234', 'THR-233', 'THR-232', 'THR-231', 'THR-230', 'THR-229', 'THR-228',
  'THR-227', 'THR-226', 'THR-225', 'THR-224', 'THR-223', 'THR-222', 'THR-221', 'THR-220',
  'THR-219', 'THR-218', 'THR-217', 'THR-216', 'THR-215', 'THR-214', 'THR-213', 'THR-212',
  'THR-211', 'THR-210', 'THR-209', 'THR-208', 'THR-207', 'THR-206', 'THR-205', 'THR-204',
  'THR-203', 'THR-202', 'THR-201',
];
const archivedOrder = ['THR-259', 'THR-258', 'THR-257', 'THR-256', 'THR-255', 'THR-254', 'THR-253', 'THR-252'];
const dreamIds = ['THR-2', 'THR-201', 'THR-208', 'THR-220', 'THR-233', 'THR-252', 'THR-253', 'THR-258', 'THR-259'];
let client: QueryClient | undefined;

beforeEach(() => {
  vi.resetModules();
  vi.doMock('react', () => ReactModule);
  vi.doMock('react/jsx-runtime', () => JSXModule);
  vi.doMock('react/jsx-dev-runtime', () => JSXDevModule);
  vi.doMock('@tanstack/react-query', () => QueryModule);
  vi.doMock('react-router-dom', () => RouterModule);
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} });
  // The actual mock freshStore and seenKeys re-evaluate in this registry.
  const fixture = allOrder.map((id, index) => ({
    thread_id: id, subject: `Fixture ${id}`, status: archivedOrder.includes(id) ? 'archived' : 'open',
    started_at: new Date(Date.UTC(2026, 0, 1, 0, 61 - index)).toISOString(),
    archived_at: null, forwarded_from_id: null, forwarded_from_kind: null,
    turn_cap: 500, turns_used: 0, summary: null, transcript_path: null,
    composed_from_dream_id: dreamIds.includes(id) ? 'DREAM-1' : null, last_speaker: null,
    pinned: id === 'THR-10' || id === 'THR-2',
    pinned_at: id === 'THR-10' || id === 'THR-2' ? '2026-01-01T00:00:00Z' : null,
    last_activity_at: null, participants: [],
  }));
  vi.doMock('@/mocks/threads', () => ({ MOCK_THREADS: fixture.map((row) => ({ ...row })) }));
  vi.doMock('@/mocks/messages', () => ({ MOCK_MESSAGES: {}, MOCK_PARTICIPANTS: {}, MOCK_REPLY_DELIVERY: {} }));
});

afterEach(async () => {
  cleanup();
  await client?.cancelQueries();
  client?.clear();
  client = undefined;
  localStorage.clear();
  sessionStorage.clear();
  for (const module of ['@/mocks/threads', '@/mocks/messages', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime', '@tanstack/react-query', 'react-router-dom']) vi.doUnmock(module);
  vi.unstubAllGlobals();
});

async function mount() {
  const { PrototypeProvider } = await import('@/design-system/providers/PrototypeProvider');
  const { ThreadsPage } = await import('./ThreadsPage');
  const { I18nProvider } = await import('@/hooks/i18n');
  const hooks = await import('@/hooks/threads');
  function Probe() {
    client = QueryModule.useQueryClient();
    const all = hooks.useThreadsInfiniteList();
    const open = hooks.useThreadsInfiniteList('open');
    const archived = hooks.useThreadsInfiniteList('archived');
    const archive = hooks.useArchiveThread('THR-10');
    const resume = hooks.useResumeThread('THR-10');
    return <>
      <output data-testid="all-pages">{JSON.stringify(all.data?.pages.map((p) => p.threads.map((r) => r.thread_id)))}</output>
      <output data-testid="open-pages">{JSON.stringify(open.data?.pages.map((p) => p.threads.map((r) => r.thread_id)))}</output>
      <output data-testid="archived-pages">{JSON.stringify(archived.data?.pages.map((p) => p.threads.map((r) => r.thread_id)))}</output>
      <output data-testid="all-summary">{JSON.stringify(all.data?.pages.at(-1)?.totals)}</output>
      <button onClick={() => void all.fetchNextPage()}>Next all</button>
      <button onClick={() => void open.fetchNextPage()}>Next open</button>
      <button onClick={() => void archive.mutateAsync({ summary: 'Archived fixture' })}>Archive fixture</button>
      <button onClick={() => void resume.mutateAsync()}>Resume fixture</button>
    </>;
  }
  render(<RouterModule.MemoryRouter initialEntries={['/__prototypes/threads-v2']}>
    <I18nProvider><PrototypeProvider><RouterModule.Routes>
      <RouterModule.Route path="/__prototypes/threads-v2" element={<><ThreadsPage /><Probe /></>} />
    </RouterModule.Routes></PrototypeProvider></I18nProvider>
  </RouterModule.MemoryRouter>);
}

function pageIds(name: string): string[][] {
  return JSON.parse(screen.getByTestId(`${name}-pages`).textContent || '[]');
}
function summary() {
  return JSON.parse(screen.getByTestId('all-summary').textContent || 'null');
}
async function click(name: string) {
  await act(async () => screen.getByRole('button', { name }).click());
}

test('prototype summary precedes exhaustion and complete order keeps Open-only pins', async () => {
  await mount();
  expect(screen.getByRole('tab', { name: /open/i })).toHaveTextContent('…');
  await waitFor(() => expect(summary()).toEqual({ open: 53, archived: 8, all: 61, dream_origin: 9 }));
  await act(async () => { fireEvent.mouseDown(screen.getByRole('tab', { name: /all/i }), { button: 0, ctrlKey: false }); });
  await waitFor(() => expect(screen.getByRole('tab', { name: /all/i })).toHaveTextContent('61'));
  expect(pageIds('all').map((p) => p.length)).toEqual([50]);
  expect(screen.queryByRole('heading', { name: 'Pinned' })).not.toBeInTheDocument();
  await click('Next all');
  await waitFor(() => expect(pageIds('all').flat()).toEqual(allOrder));
  expect(pageIds('all').map((p) => p.length)).toEqual([50, 11]);
  expect(screen.getAllByRole('link').map((row) => row.getAttribute('href'))).toEqual(allOrder.map((id) => `/__prototypes/threads-v2/${id}`));
  await click('Next all');
  expect(pageIds('all').flat()).toEqual(allOrder);
  await click('Next open');
  await waitFor(() => expect(pageIds('open').flat()).toEqual(['THR-10', 'THR-2', ...ordinaryOpen]));
  await act(async () => { fireEvent.mouseDown(screen.getByRole('tab', { name: /open/i }), { button: 0, ctrlKey: false }); });
  expect(await screen.findByRole('heading', { name: 'Pinned' })).toBeVisible();
  await act(async () => { fireEvent.mouseDown(screen.getByRole('tab', { name: /archived/i }), { button: 0, ctrlKey: false }); });
  await waitFor(() => expect(pageIds('archived').flat()).toEqual(archivedOrder));
  expect(screen.queryByRole('heading', { name: 'Pinned' })).not.toBeInTheDocument();
});

test('prototype archive and resume refresh already-mounted summary and membership', async () => {
  await mount();
  await waitFor(() => expect(summary()).toEqual({ open: 53, archived: 8, all: 61, dream_origin: 9 }));
  await click('Next all');
  await click('Next open');
  await waitFor(() => expect(pageIds('all').flat()).toEqual(allOrder));
  await click('Archive fixture');
  await waitFor(() => expect(summary()).toEqual({ open: 52, archived: 9, all: 61, dream_origin: 9 }));
  await waitFor(() => expect(pageIds('open').flat()).toEqual(['THR-2', ...ordinaryOpen]));
  expect(pageIds('all').flat()).toEqual(allOrder);
  expect(pageIds('archived').flat()).toContain('THR-10');
  await click('Resume fixture');
  await waitFor(() => expect(summary()).toEqual({ open: 53, archived: 8, all: 61, dream_origin: 9 }));
  await waitFor(() => expect(pageIds('open').flat()).toEqual(['THR-10', 'THR-2', ...ordinaryOpen]));
  expect(pageIds('all').flat()).toEqual(allOrder);
  expect(pageIds('archived').flat()).toEqual(archivedOrder);
});
