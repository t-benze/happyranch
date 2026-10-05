import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { describe, expect, test, vi } from 'vitest';
import { Routes, Route } from 'react-router-dom';
import { AppRoutes } from '@/routes';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { KbCandidateCard } from './KbCandidateCard';
import { ComposeKbEntryDialog } from './ComposeKbEntryDialog';

const mermaidChunk = vi.hoisted(() => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  return { ready, release };
});
// External library-load delay; actual Markdown, lazy import and callers stay real.
vi.mock('mermaid', async () => {
  await mermaidChunk.ready;
  return { default: { initialize: vi.fn(), render: vi.fn(async () => ({ svg: '<svg data-w5a-diagram="loaded"></svg>' })) } };
});

const entry = { slug: 'raw-slug', title: 'Authored «raw» title', type: 'RAW_Type', topic: 'raw_topic', tags: ['Raw_Tag'], body: 'Authored «raw» body', updated_at: new Date().toISOString(), authored_by: 'Raw_Agent', source_task: 'TASK-0042', related_entries: ['Raw_Related'] };
const candidate = { id: 42, dream_id: 'DREAM-0042', agent_name: 'Raw_Agent', slug: 'Raw_Slug', title: 'Raw candidate', topic: 'Raw_Topic', rationale: 'Raw rationale', body_markdown: 'Raw body', status: 'pending', promoted_kb_slug: null, created_at: '', updated_at: '' };
function base() {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/x' }] })), http.get('/api/v1/orgs/alpha/dashboard/summary', () => HttpResponse.json({ org_age_days: 12 })), http.get('/api/v1/orgs/alpha/kb', () => HttpResponse.json({ entries: [entry] })), http.get('/api/v1/orgs/alpha/kb/stats', () => HttpResponse.json({ entries: [{ slug: entry.slug, view_count: 1000 }] })), http.get('/api/v1/orgs/alpha/dreams', () => HttpResponse.json({ dreams: [] })), http.get('/api/v1/orgs/alpha/kb/raw-slug', () => HttpResponse.json(entry)));
}
function switchLocale(locale: string) { act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'happyranch.ui.locale', newValue: locale }))); }

describe('KB bilingual mounted chrome', () => {
  test.each(['en', 'zh-CN'] as const)('list/detail preserve authored bytes in %s', async locale => {
    base();
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/kb/raw-slug', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText(entry.body);
    expect(screen.getByText(locale === 'en' ? 'What the org has learned' : '组织积累的知识', { selector: 'h1' })).toHaveTextContent(locale === 'en' ? 'What the org has learned' : '组织积累的知识');
    expect(screen.getByText(locale === 'en' ? 'ALL ENTRIES · 1 DOCUMENT' : '所有条目 · 1 篇文档')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveAccessibleName(entry.title);
    expect(screen.getByText(locale === 'en' ? /Authored by Raw_Agent/ : /作者：Raw_Agent/)).toBeInTheDocument();
    expect(screen.getAllByText(entry.type).every(node => node.textContent === entry.type)).toBe(true);
    expect(screen.getAllByText(entry.title).every(node => node.textContent === entry.title)).toBe(true);
    expect(screen.getByText(locale === 'en' ? 'viewed 1,000× (CLI)' : '已查看 1,000 次（CLI）')).toBeInTheDocument();
  });
  test.each(['accept', 'dismiss'] as const)('candidate %s generated failure retranslates without another mutation', async action => {
    const english = action === 'accept' ? 'Accept failed — retry' : 'Dismiss failed — retry';
    const chinese = action === 'accept' ? '接受失败 — 请重试' : '驳回失败 — 请重试';
    base(); let posts = 0;
    server.use(http.post(`/api/v1/orgs/alpha/dreams/candidates/42/${action}`, () => { posts++; return HttpResponse.json({}, { status: 500 }); }));
    renderWithProviders(<Routes><Route path="/orgs/:slug/kb" element={<KbCandidateCard candidate={candidate} />} /></Routes>, { route: '/orgs/alpha/kb', i18n: { adapter: savedLocaleAdapter('en') } });
    await userEvent.click(screen.getByRole('button', { name: action === 'accept' ? 'Accept' : 'Dismiss' }));
    await screen.findByText(english);
    switchLocale('zh-CN');
    expect(screen.getByText(chinese)).toBeInTheDocument();
    expect(screen.getByText(candidate.rationale, { exact: false }).textContent).toBe('“Raw rationale”');
    switchLocale('en'); expect(screen.getByText(english)).toBeInTheDocument(); expect(posts).toBe(1);
  });
  test.each(['en', 'zh-CN'] as const)('compose labels and close control in %s retain gated form inputs', locale => {
    renderWithProviders(<ComposeKbEntryDialog onClose={() => {}} />, { route: '/orgs/alpha/kb', i18n: { adapter: savedLocaleAdapter(locale) } });
    expect(screen.getByLabelText(locale === 'en' ? 'Title' : '标题')).toBeRequired();
    expect(screen.getByLabelText(locale === 'en' ? 'Body (Markdown)' : '正文（Markdown）')).toBeRequired();
    expect(screen.getByRole('button', { name: locale === 'en' ? 'Close' : '关闭' })).toBeInTheDocument();
  });
  test.each(['en', 'zh-CN'] as const)('empty/error + Retry chrome in %s', async locale => {
    base();
    server.use(http.get('/api/v1/orgs/alpha/kb', () => HttpResponse.json({ entries: [] })));
    const mounted = renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/kb', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText(locale === 'en' ? 'No entries yet' : '暂无条目');
    mounted.unmount();
    server.use(http.get('/api/v1/orgs/alpha/kb', () => HttpResponse.json({ detail: 'RAW_HTTP' }, { status: 500 })));
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/kb', i18n: { adapter: savedLocaleAdapter(locale) } });
    await screen.findByText(locale === 'en' ? /Could not load Knowledge/ : /无法加载知识库/);
    expect(screen.getByRole('button', { name: locale === 'en' ? 'Retry' : '重试' })).toBeInTheDocument();
  });

  test.each(['en', 'zh-CN'] as const)('loading accessibility chrome in %s', async locale => {
    base(); server.use(http.get('/api/v1/orgs/alpha/kb', async () => { await delay('infinite'); return HttpResponse.json({}); }));
    renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/kb', i18n: { adapter: savedLocaleAdapter(locale) } });
    expect(await screen.findByLabelText(locale === 'en' ? 'Loading entries' : '正在加载条目')).toBeInTheDocument();
  });

});

test('KB mounted Mermaid loading copy switches without replacing the detail body or fetching', async () => {
  base();
  const body = 'Rendering diagram…\n\n正在渲染图表…\n\n```mermaid\nflowchart LR; A-->B\n```';
  server.use(http.get('/api/v1/orgs/alpha/kb/raw-slug', () => HttpResponse.json({ ...entry, body })));
  renderWithProviders(<AppRoutes />, { route: '/orgs/alpha/kb/raw-slug', i18n: { adapter: savedLocaleAdapter('en') } });
  const dialog = await screen.findByRole('dialog', { name: entry.title });
  try {
    await waitFor(() => expect(dialog.querySelector('.gl-prose-mermaid-loading')).not.toBeNull());
    const fallback = dialog.querySelector('.gl-prose-mermaid-loading');
    const prose = dialog.querySelector('.gl-prose');
    const raw = screen.getByText('Rendering diagram…', { selector: 'p' });
    const requests: string[] = [];
    const observe = ({ request }: { request: Request }) => requests.push(request.url);
    server.events.on('request:start', observe);
    try {
      for (const locale of ['zh-CN', 'en'] as const) {
        switchLocale(locale);
        expect(fallback?.textContent).toBe(locale === 'en' ? 'Rendering diagram…' : '正在渲染图表…');
        expect(dialog.querySelector('.gl-prose-mermaid-loading')).toBe(fallback);
        expect(screen.getByRole('dialog', { name: entry.title })).toBe(dialog);
        expect(dialog.querySelector('.gl-prose')).toBe(prose);
        expect(screen.getByText('Rendering diagram…', { selector: 'p' })).toBe(raw);
        expect(screen.getByText('正在渲染图表…', { selector: 'p' })).toBeInTheDocument();
      }
      expect(requests).toEqual([]);
    } finally { server.events.removeListener('request:start', observe); }
  } finally { await act(async () => mermaidChunk.release()); }
  await waitFor(() => expect(dialog.querySelector('svg[data-w5a-diagram]')).not.toBeNull());
});
