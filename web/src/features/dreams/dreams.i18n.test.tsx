/**
 * THR-118 W4a-1 — Dreams route family (feed + rail + detail drawer) i18n.
 *
 * Renders the real routed page under the real I18nProvider and asserts:
 *  - zh-CN product copy for the feed (populated / empty / error), the overview
 *    rail and the detail drawer (status, counts, candidates, review gate);
 *  - daemon-supplied values (dream ids, agent names, dates, summaries,
 *    transcript, error text, candidate title/slug/topic) stay byte-identical;
 *  - a locale switch keeps the same card/drawer/button nodes and focus, and
 *    issues zero requests;
 *  - the Accept/Dismiss diagnostic boundary: a recognized daemon code maps to
 *    catalog copy (re-translated in place), an unknown code and a code-less
 *    string detail stay verbatim, and an empty code / non-string detail falls
 *    back to the localized failure copy.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

const BASE = {
  scheduled_for: '2026-06-18T03:00:00Z',
  window_start: null,
  window_end: '2026-06-18T03:10:00Z',
  started_at: '2026-06-18T03:00:02Z',
  ended_at: null,
  transcript_path: null,
  founder_thread_id: null,
  error: null,
  summary: null,
};

const QUIET = {
  ...BASE,
  dream_id: 'DREAM-0012',
  agent_name: 'engineering_manager',
  local_date: '2026-06-18',
  status: 'completed',
  summary: 'Routine nightly reflection. No issues found.',
  new_learnings_count: 2,
  kb_candidate_count: 0,
};

const WITH_CANDIDATES = {
  ...BASE,
  dream_id: 'DREAM-0011',
  agent_name: 'product_lead',
  local_date: '2026-06-18',
  status: 'completed',
  summary: 'Identified a recurring pattern.',
  new_learnings_count: 1,
  kb_candidate_count: 1,
  founder_thread_id: 'THR-010',
};

const FAILED = {
  ...BASE,
  dream_id: 'DREAM-0009',
  agent_name: 'dev_agent',
  local_date: '2026-06-17',
  status: 'failed',
  new_learnings_count: 0,
  kb_candidate_count: 0,
  error: 'Executor API returned 503',
};

const DETAIL = {
  ...WITH_CANDIDATES,
  transcript: '## Reflection\n\nIdentified a recurring pattern.',
  kb_candidates: [
    {
      id: 7,
      dream_id: 'DREAM-0011',
      agent_name: 'product_lead',
      slug: 'spanish-after-hours',
      title: 'Spanish after-hours routing',
      topic: 'support',
      rationale: 'Seen three times this week.',
      body_markdown: 'Route Spanish tickets after 18:00 to the on-call queue.',
      status: 'pending',
      promoted_kb_slug: null,
      created_at: '2026-06-18T03:05:00Z',
      updated_at: '2026-06-18T03:05:00Z',
    },
  ],
};

function stub(mode: { dreams?: unknown[]; listError?: boolean } = {}) {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/dreams`, () =>
      mode.listError
        ? HttpResponse.json({ detail: 'boom' }, { status: 500 })
        : HttpResponse.json({ dreams: mode.dreams ?? [QUIET, WITH_CANDIDATES, FAILED] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/dreams/DREAM-0011`, () => HttpResponse.json(DETAIL)),
  );
}

function stubAccept(body: Record<string, unknown>, status = 400) {
  server.use(
    http.post(`/api/v1/orgs/${SLUG}/dreams/candidates/7/accept`, () =>
      HttpResponse.json(body, { status }),
    ),
  );
}

function mount(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route: `/orgs/${SLUG}/dreams`, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

async function switchLocale(to: 'en' | 'zh-CN') {
  await act(async () => {
    fireEvent.click(screen.getByTestId(`test-set-locale-${to}`));
  });
}

async function countRequests(fn: () => Promise<void>): Promise<string[]> {
  const seen: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    seen.push(`${request.method} ${new URL(request.url).pathname}`);
  };
  server.events.on('request:start', listener);
  try {
    await fn();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
  } finally {
    server.events.removeListener('request:start', listener);
  }
  return seen;
}

async function openDetail(): Promise<HTMLElement> {
  const card = (await screen.findByText('DREAM-0011')).closest('button');
  if (!card) throw new Error('dream card not found');
  fireEvent.click(card);
  const accept = await screen.findByRole('button', { name: /^(Accept|接受)$/ });
  const drawer = accept.closest('[role="dialog"]');
  if (!(drawer instanceof HTMLElement)) throw new Error('accept is not inside the drawer');
  return drawer;
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
});

describe('Dreams feed i18n', () => {
  test('populated feed + rail: zh-CN chrome, verbatim daemon values, same card node and zero requests', async () => {
    stub();
    mount('zh-CN');

    expect(await screen.findByText('每夜反思 · 2 夜')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '组织在这里沉淀思考。' })).toBeInTheDocument();
    expect(await screen.findByText('3 个梦境')).toBeInTheDocument();
    expect(screen.getAllByText('已完成').length).toBe(2);
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('安静的梦境 — 无需上报 · 已保存私有学习')).toBeInTheDocument();
    expect(screen.getByText('打开反思线程 →')).toBeInTheDocument();
    expect(screen.getAllByText('未打开反思线程').length).toBe(2);
    const rail = screen.getByRole('complementary', { name: '梦境概览' });
    expect(within(rail).getByText('3 次反思')).toBeInTheDocument();
    expect(within(rail).getByText('3 条学习')).toBeInTheDocument();
    expect(within(rail).getByText('1 个候选')).toBeInTheDocument();
    expect(within(rail).getByText('按设置中配置的计划运行。')).toBeInTheDocument();
    // Daemon values stay byte-verbatim.
    expect(screen.getByText('engineering_manager')).toBeInTheDocument();
    expect(screen.getByText('Routine nightly reflection. No issues found.')).toBeInTheDocument();
    expect(screen.getByText('Executor API returned 503')).toBeInTheDocument();
    expect(screen.getAllByText('2026-06-18').length).toBe(2);

    const card = screen.getByText('DREAM-0012').closest('button');
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(screen.getByText('NIGHTLY REFLECTION · 2 NIGHTS')).toBeInTheDocument();
      expect(screen.getByText('3 dreams')).toBeInTheDocument();
      expect(screen.getByText('3 reflections')).toBeInTheDocument();
      expect(screen.getByText('DREAM-0012').closest('button')).toBe(card);
      await switchLocale('zh-CN');
      expect(screen.getByText('DREAM-0012').closest('button')).toBe(card);
      expect(screen.getByText('每夜反思 · 2 夜')).toBeInTheDocument();
    });
    expect(requests).toEqual([]);
  });

  test('empty and error states are localized', async () => {
    stub({ dreams: [] });
    const { unmount } = mount('zh-CN');
    expect(await screen.findByText('暂无梦境')).toBeInTheDocument();
    expect(screen.getByText('每夜反思 · 0 夜')).toBeInTheDocument();
    unmount();
    stub({ listError: true });
    mount('zh-CN');
    expect(await screen.findByText('无法加载梦境')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });
});

describe('Dream detail drawer i18n', () => {
  test('zh-CN drawer copy, verbatim values, same drawer/button nodes, focus and zero requests across switches', async () => {
    stub();
    mount('zh-CN');
    const drawer = await openDetail();

    expect(within(drawer).getByText('product_lead · 2026-06-18')).toBeInTheDocument();
    expect(within(drawer).getByText('来自梦境 · 由 product_lead 提议 · 待审核')).toBeInTheDocument();
    expect(within(drawer).getByText('知识候选')).toBeInTheDocument();
    expect(within(drawer).getByText('1 条学习')).toBeInTheDocument();
    expect(within(drawer).getByText('1 个待审核')).toBeInTheDocument();
    expect(within(drawer).getByText('Spanish after-hours routing')).toBeInTheDocument();
    expect(within(drawer).getByText('spanish-after-hours')).toBeInTheDocument();
    expect(within(drawer).getByRole('button', { name: '忽略' })).toBeInTheDocument();

    const accept = within(drawer).getByRole('button', { name: '接受' });
    accept.focus();
    const requests = await countRequests(async () => {
      await switchLocale('en');
      expect(within(drawer).getByText('from dream · proposed by product_lead · pending review')).toBeInTheDocument();
      expect(within(drawer).getByText('Knowledge candidates')).toBeInTheDocument();
      expect(within(drawer).getByRole('button', { name: 'Accept' })).toBe(accept);
      expect(document.activeElement).toBe(accept);
      await switchLocale('zh-CN');
      expect(within(drawer).getByRole('button', { name: '接受' })).toBe(accept);
      expect(document.activeElement).toBe(accept);
    });
    expect(requests).toEqual([]);
  });

  test('recognized accept code maps to catalog copy and re-translates in place', async () => {
    stub();
    stubAccept({ detail: { code: 'candidate_already_decided', status: 'promoted' } });
    mount('zh-CN');
    const drawer = await openDetail();
    fireEvent.click(within(drawer).getByRole('button', { name: '接受' }));
    expect(await within(drawer).findByText('该候选已被处理。')).toBeInTheDocument();
    await switchLocale('en');
    expect(within(drawer).getByText('This candidate has already been decided.')).toBeInTheDocument();
  });

  test('unknown code and code-less string detail stay verbatim', async () => {
    stub();
    stubAccept({ detail: { code: 'kb_write_conflict' } });
    mount('zh-CN');
    const drawer = await openDetail();
    fireEvent.click(within(drawer).getByRole('button', { name: '接受' }));
    expect(await within(drawer).findByText('kb_write_conflict')).toBeInTheDocument();

    stubAccept({ detail: 'KB store is read-only' }, 503);
    fireEvent.click(within(drawer).getByRole('button', { name: '接受' }));
    expect(await within(drawer).findByText('KB store is read-only')).toBeInTheDocument();
    expect(within(drawer).queryByText(/API 503/)).toBeNull();
  });

  test('empty code / non-string detail falls back to the localized failure copy', async () => {
    stub();
    stubAccept({ detail: { code: '' } }, 500);
    mount('zh-CN');
    const drawer = await openDetail();
    fireEvent.click(within(drawer).getByRole('button', { name: '接受' }));
    expect(await within(drawer).findByText('接受失败 — 请重试')).toBeInTheDocument();
    await waitFor(() => expect(within(drawer).queryByText(/API 500/)).toBeNull());
    await switchLocale('en');
    expect(within(drawer).getByText('Accept failed — retry')).toBeInTheDocument();
  });
});
