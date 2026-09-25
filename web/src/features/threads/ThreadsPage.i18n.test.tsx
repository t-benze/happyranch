/**
 * THR-118 W3a — ThreadsPage (list + detail + composer) i18n.
 *
 * Renders the real routed page under the real I18nProvider and asserts:
 *  - zh-CN product copy for the list (loading / error + retry / empty /
 *    filter-empty / populated incl. the Pinned section and selection) and the
 *    detail (loading / error / no-messages / populated rails);
 *  - authored content (thread titles, message Markdown, participant/agent
 *    names, thread/task IDs, attachment filenames) stays byte-identical;
 *  - a locale switch keeps the composer's textarea node, draft value, focus and
 *    attachment chip, issues zero requests, and a later send posts once;
 *  - a mapped composer error retranslates in place while raw diagnostics
 *    (`HTTP 500`, a raw string equal to English catalog text, and the empty
 *    string) render exactly.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';
const AUTHORED_SUBJECT = 'Ship `v2` — **now** (EN title kept)';
const AUTHORED_BODY = 'Plan: **bold** and `code` for engineering_manager';

function mkThread(
  id: string,
  subject: string,
  overrides: Partial<{
    status: 'open' | 'archived';
    pinned: boolean;
    composed_from_dream_id: string | null;
    participants: string[];
  }> = {},
) {
  return {
    thread_id: id,
    subject,
    status: 'open' as 'open' | 'archived',
    started_at: '2026-05-14T00:00:00Z',
    archived_at: null,
    forwarded_from_id: null,
    forwarded_from_kind: null,
    turn_cap: 500,
    turns_used: 1,
    summary: null,
    transcript_path: null,
    composed_from_dream_id: null as string | null,
    last_speaker: 'engineering_manager',
    pinned: false,
    pinned_at: null,
    last_activity_at: '2026-05-14T00:00:00Z',
    participants: ['founder', 'engineering_manager'],
    ...overrides,
  };
}

function mkMessage(seq: number, speaker: string, body: string, attachments: unknown[] = []) {
  return {
    seq,
    speaker,
    kind: 'message' as const,
    body_markdown: body,
    decline_reason: null,
    system_payload: null,
    attachments,
    created_at: '2026-05-14T00:00:00Z',
    responder_status: [],
  };
}

function stubBase() {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () => HttpResponse.json({ agents: [] })),
    http.get(`/api/v1/orgs/${SLUG}/threads/events`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/tokens`, () => HttpResponse.json({ rollup: [] })),
  );
}

function stubList(threads: ReturnType<typeof mkThread>[]) {
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      return HttpResponse.json({
        threads: status ? threads.filter((t) => t.status === status) : threads,
      });
    }),
  );
}

function stubDetail(
  thread: ReturnType<typeof mkThread>,
  messages: ReturnType<typeof mkMessage>[],
  tasks: unknown[] = [],
) {
  const id = thread.thread_id;
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/${id}`, () =>
      HttpResponse.json({ ...thread, messages }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${id}/messages`, () =>
      HttpResponse.json({ messages, has_more: false, next_since_seq: 0, reply_delivery: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/${id}/tasks`, () => HttpResponse.json(tasks)),
    http.get(`/api/v1/orgs/${SLUG}/threads/${id}/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );
}

function mount(route: string, locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

/** Switch locale without moving focus (fireEvent.click never focuses). */
async function switchLocale(to: 'en' | 'zh-CN') {
  await act(async () => {
    fireEvent.click(screen.getByTestId(`test-set-locale-${to}`));
  });
}

/** Record every request the page issues while `fn` runs. */
async function countRequests(fn: () => Promise<void>): Promise<string[]> {
  const seen: string[] = [];
  const listener = ({ request }: { request: Request }) => {
    seen.push(`${request.method} ${new URL(request.url).pathname}`);
  };
  server.events.on('request:start', listener);
  try {
    await fn();
    // Let any effect-triggered fetch/refetch surface before we stop counting.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
  } finally {
    server.events.removeListener('request:start', listener);
  }
  return seen;
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
  localStorage.clear();
  stubBase();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ThreadsPage list i18n (zh-CN)', () => {
  test('loading then populated list: chrome localized, Pinned section, authored data verbatim, selection', async () => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    const threads = [
      mkThread('THR-10', AUTHORED_SUBJECT, { pinned: true, composed_from_dream_id: 'DRM-1' }),
      mkThread('THR-2', 'Ordinary subject'),
      mkThread('THR-3', 'Archived subject', { status: 'archived' }),
    ];
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
        await held;
        const status = new URL(request.url).searchParams.get('status');
        return HttpResponse.json({ threads: threads.filter((t) => t.status === status) });
      }),
    );
    stubDetail(threads[0], [mkMessage(1, 'founder', AUTHORED_BODY)]);
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/threads`, 'zh-CN');

    // Loading: localized chrome + in-progress bucket counts.
    expect(await screen.findByRole('heading', { name: '全组织的会话' })).toBeInTheDocument();
    const tabs = screen.getByRole('tablist', { name: '状态筛选' });
    expect(within(tabs).getByRole('tab', { name: /^全部/ })).toBeInTheDocument();
    expect(within(tabs).getByRole('tab', { name: /^开放/ })).toBeInTheDocument();
    expect(within(tabs).getByRole('tab', { name: /^已归档/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('筛选…')).toHaveAccessibleName('筛选会话');
    expect(screen.getByRole('button', { name: '新建会话' })).toHaveTextContent('+ 新建会话');
    release();

    // Populated.
    expect(await screen.findByText('3 个会话 · 1 个由梦境开启')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '已置顶' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '会话' })).toBeInTheDocument();
    const pinnedRow = screen.getByRole('link', { name: new RegExp('THR-10') });
    expect(within(pinnedRow).getByText(AUTHORED_SUBJECT)).toBeInTheDocument();
    expect(within(pinnedRow).getByText('开放')).toBeInTheDocument();
    expect(within(pinnedRow).getByText('来自梦境')).toBeInTheDocument();
    expect(within(pinnedRow).getByText('最后发言')).toBeInTheDocument();
    // Agent names / ids stay verbatim; relative time is localized.
    expect(within(pinnedRow).getByText('engineering_manager')).toBeInTheDocument();
    expect(within(pinnedRow).getByText('founder · engineering_manager')).toBeInTheDocument();
    expect(within(pinnedRow).getByText(/^\d+ 天前$/)).toBeInTheDocument();
    // English copy of the same keys is absent.
    expect(screen.queryByText('Pinned')).not.toBeInTheDocument();
    expect(screen.queryByText(/THREADS ·/)).not.toBeInTheDocument();
    expect(screen.queryByText('from dream')).not.toBeInTheDocument();

    // Selection navigates to the detail; the authored title is byte-identical.
    await user.click(pinnedRow);
    expect((await screen.findByText('bold')).closest('article')?.textContent).toContain(
      'Plan: bold and code for engineering_manager',
    );
    expect(screen.getAllByText(AUTHORED_SUBJECT).length).toBeGreaterThan(0);
    expect(screen.getByRole('link', { name: '‹ 全部会话' })).toBeInTheDocument();
  });

  test('error + retry, empty and filter-empty states are localized', async () => {
    let fail = true;
    const threads = [mkThread('THR-7', 'Seventh subject')];
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
        if (fail) return HttpResponse.json({ detail: 'boom' }, { status: 500 });
        const status = new URL(request.url).searchParams.get('status');
        return HttpResponse.json({ threads: threads.filter((t) => t.status === status) });
      }),
    );
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/threads`, 'zh-CN');

    expect(await screen.findByText('无法加载会话', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('后端错误导致会话无法加载。')).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('Seventh subject')).toBeInTheDocument();

    // Filter-empty (a non-matching filter over a populated bucket).
    await user.type(screen.getByPlaceholderText('筛选…'), 'zzz-no-match');
    expect(await screen.findByText('没有与筛选条件匹配的会话。')).toBeInTheDocument();
    expect(screen.getByText('暂无会话')).toBeInTheDocument();
    expect(screen.getByDisplayValue('zzz-no-match')).toBeInTheDocument();

    // Plain empty bucket (Archived has none).
    await user.clear(screen.getByPlaceholderText('筛选…'));
    await user.click(screen.getByRole('tab', { name: /^已归档/ }));
    expect(await screen.findByText('新建一个会话，与你的智能体开始广播对话。')).toBeInTheDocument();
  });
});

describe('ThreadsPage detail i18n (zh-CN)', () => {
  test('detail loading then error with retry are localized', async () => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => { release = resolve; });
    stubList([]);
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-9`, async () => {
        await held;
        return HttpResponse.json({ detail: 'nope' }, { status: 500 });
      }),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-9/messages`, () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-9/tasks`, () => HttpResponse.json([])),
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-9/tail`, () =>
        HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
      ),
    );
    mount(`/orgs/${SLUG}/threads/THR-9`, 'zh-CN');
    expect(await screen.findByText('正在加载消息…')).toBeInTheDocument();
    release();
    expect(await screen.findByText('加载会话失败。', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '‹ 全部会话' })).toBeInTheDocument();
  });

  test('no-messages state is localized', async () => {
    stubList([]);
    stubDetail(mkThread('THR-4', 'Quiet subject'), []);
    mount(`/orgs/${SLUG}/threads/THR-4`, 'zh-CN');
    expect(await screen.findByText('暂无消息。')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '撰写跟进消息' })).toHaveAttribute(
      'placeholder',
      '向会话发送消息 — 所有参与者都能看到（广播）',
    );
  });

  test('populated detail: header, actions, rails localized; authored content verbatim', async () => {
    const thread = mkThread('THR-5', AUTHORED_SUBJECT, { composed_from_dream_id: 'DRM-9' });
    stubList([]);
    stubDetail(
      thread,
      [
        mkMessage(1, 'founder', AUTHORED_BODY),
        mkMessage(2, 'engineering_manager', 'Reply **ok**', [
          {
            thread_attachment_id: 'TA-1',
            artifact_name: 'THR-5-report.pdf',
            display_name: 'report final.pdf',
            content_type: 'application/pdf',
            size_bytes: 10,
          },
        ]),
      ],
      [
        {
          id: 'TASK-42',
          status: 'in_progress',
          brief: 'b',
          assigned_agent: 'engineering_manager',
          created_at: '2026-05-14T00:00:00Z',
          parent_task_id: null,
        },
      ],
    );
    mount(`/orgs/${SLUG}/threads/THR-5`, 'zh-CN');

    expect(await screen.findByText('活跃')).toBeInTheDocument();
    expect(screen.getAllByText(AUTHORED_SUBJECT).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: '重命名' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '置顶' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '归档' })).toBeInTheDocument();

    const rail = screen.getByRole('complementary', { name: '会话属性' });
    expect(within(rail).getByText('参与者')).toBeInTheDocument();
    expect(within(rail).getByText('你')).toBeInTheDocument();
    expect(within(rail).getByText('创始人')).toBeInTheDocument();
    expect(within(rail).getByText('engineering_manager')).toBeInTheDocument();
    expect(within(rail).getByRole('button', { name: '移除 engineering_manager' })).toBeInTheDocument();
    expect(within(rail).getByRole('button', { name: /邀请参与者/ })).toBeInTheDocument();
    expect(within(rail).getByText('关联任务')).toBeInTheDocument();
    expect(await within(rail).findByRole('link', { name: 'TASK-42' })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/tasks/TASK-42`,
    );
    // Stored task status renders verbatim (machine value, never paraphrased).
    expect(within(rail).getByText('in_progress')).toBeInTheDocument();
    expect(within(rail).getByText('此会话')).toBeInTheDocument();
    expect(within(rail).getByText('成本')).toBeInTheDocument();
    expect(within(rail).getByText('未计量')).toBeInTheDocument();
    expect(within(rail).getByText('开启于')).toBeInTheDocument();
    expect(within(rail).getByText('产物')).toBeInTheDocument();
    expect(within(rail).getByText('report final.pdf')).toBeInTheDocument();
    expect(within(rail).getByText('THR-5')).toBeInTheDocument();
    expect(within(rail).getByText('来源')).toBeInTheDocument();
    expect(within(rail).getByText('梦境')).toBeInTheDocument();

    // Message Markdown renders the authored text unchanged.
    const bold = screen.getByText('bold');
    expect(bold.tagName).toBe('STRONG');
    expect(bold.closest('article')?.textContent).toContain('Plan: bold and code for engineering_manager');
    expect(screen.getByText('ok').tagName).toBe('STRONG');

    // Composer chrome.
    expect(screen.getByRole('button', { name: '发送' })).toHaveAttribute('title', '发送（Enter）');
    expect(screen.getByLabelText('添加附件')).toBeInTheDocument();

    // English copies of these keys are absent.
    for (const english of ['Participants', 'Linked tasks', 'This thread', 'Artifacts', 'Origin', 'not metered']) {
      expect(screen.queryByText(english)).not.toBeInTheDocument();
    }
  });
});

describe('ThreadsPage composer across a locale switch', () => {
  test('draft, attachment chip and focus survive en -> zh-CN -> en with zero requests; one send posts once', async () => {
    stubList([]);
    stubDetail(mkThread('THR-1', 'Composer subject'), [mkMessage(1, 'founder', 'hi')]);
    const sends: unknown[] = [];
    let uploads = 0;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, ({ request }) => {
        uploads += 1;
        const name = new URL(request.url).searchParams.get('name') ?? '';
        return HttpResponse.json({ name, size_bytes: 3, modified_at: 'now' });
      }),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/send`, async ({ request }) => {
        sends.push(await request.json());
        return HttpResponse.json({ thread_id: 'THR-1', seq: 2 });
      }),
    );
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/threads/THR-1`, 'en');

    const textarea = (await screen.findByRole('textbox', { name: 'Compose follow-up' })) as HTMLTextAreaElement;
    await screen.findByText('No tasks dispatched from this thread yet');
    const draft = 'Ship **v2** with `flag` now';
    await user.type(textarea, draft);
    await user.upload(
      screen.getByLabelText('Attach files'),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    const chip = screen.getByRole('button', { name: 'Remove attachment' }).closest('span')!;
    textarea.focus();
    expect(document.activeElement).toBe(textarea);

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN');
      const zhTextarea = screen.getByRole('textbox', { name: '撰写跟进消息' });
      expect(zhTextarea).toBe(textarea);
      expect(zhTextarea).toHaveValue(draft);
      expect(document.activeElement).toBe(textarea);
      const zhChip = screen.getByRole('button', { name: '移除附件' }).closest('span');
      expect(zhChip).toBe(chip);
      expect(within(chip).getByText('note.txt')).toBeInTheDocument();

      await switchLocale('en');
      expect(screen.getByRole('textbox', { name: 'Compose follow-up' })).toBe(textarea);
      expect(textarea).toHaveValue(draft);
      expect(document.activeElement).toBe(textarea);
      expect(screen.getByRole('button', { name: 'Remove attachment' }).closest('span')).toBe(chip);
    });
    expect(requests).toEqual([]);
    expect(sends).toHaveLength(0);
    expect(uploads).toBe(0);

    // Positive control: the same recorder DOES observe the real send, so the
    // empty switch-window list above is not vacuous.
    const sendWindow = await countRequests(async () => {
      await user.click(screen.getByRole('button', { name: 'Send' }));
      await waitFor(() => expect(sends).toHaveLength(1));
    });
    expect(sendWindow).toContain(`POST /api/v1/orgs/${SLUG}/threads/THR-1/send`);
    expect(sendWindow.filter((r) => r.endsWith('/send'))).toHaveLength(1);
    expect(uploads).toBe(1);
    expect((sends[0] as { body_markdown: string }).body_markdown).toBe(draft);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60));
    });
    expect(sends).toHaveLength(1);
  });

  test('mapped error retranslates in place; raw diagnostics stay exact', async () => {
    stubList([]);
    stubDetail(mkThread('THR-1', 'Composer subject'), [mkMessage(1, 'founder', 'hi')]);
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, () =>
        HttpResponse.json({ detail: { code: 'artifact_too_large' } }, { status: 413 }),
      ),
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-1/send`, () =>
        HttpResponse.json({ detail: { code: 'some_unmapped_code' } }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    mount(`/orgs/${SLUG}/threads/THR-1`, 'en');
    const textarea = await screen.findByRole('textbox', { name: 'Compose follow-up' });
    await user.type(textarea, 'draft');

    // (1) Raw `HTTP 500` from an unmapped send code — no attachment, no label.
    await user.click(screen.getByRole('button', { name: 'Send' }));
    const rawSlot = await screen.findByTestId('composer-error');
    expect(rawSlot.textContent).toBe('HTTP 500');
    await switchLocale('zh-CN');
    expect(screen.getByTestId('composer-error')).toBe(rawSlot);
    expect(rawSlot.textContent).toBe('HTTP 500');
    await switchLocale('en');

    // (2) Mapped upload error: label + mapped detail retranslate in place.
    await user.upload(
      screen.getByLabelText('Attach files'),
      new File(['abc'], 'note.txt', { type: 'text/plain' }),
    );
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(screen.getByTestId('composer-error').textContent).toBe(
        'note.txt: That file is too large to upload.',
      ),
    );
    const slot = screen.getByTestId('composer-error');
    await switchLocale('zh-CN');
    expect(screen.getByTestId('composer-error')).toBe(slot);
    expect(slot.textContent).toBe('note.txt：文件过大，无法上传。');
    await switchLocale('en');
    expect(slot.textContent).toBe('note.txt: That file is too large to upload.');

    // (3) Raw diagnostic byte-equal to the English catalog text stays English.
    const originalSet = FormData.prototype.set;
    const setSpy = vi.spyOn(FormData.prototype, 'set').mockImplementation(function () {
      throw 'That file is too large to upload.';
    });
    await user.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(setSpy).toHaveBeenCalled());
    await switchLocale('zh-CN');
    await waitFor(() =>
      expect(screen.getByTestId('composer-error').textContent).toBe(
        'note.txt：That file is too large to upload.',
      ),
    );

    // (4) Empty raw diagnostic: the slot still renders; detail is exactly ''.
    setSpy.mockImplementation(function () {
      throw '';
    });
    await user.click(screen.getByRole('button', { name: '发送' }));
    await waitFor(() =>
      expect(screen.getByTestId('composer-error').textContent).toBe('note.txt：'),
    );
    await switchLocale('en');
    expect(screen.getByTestId('composer-error').textContent).toBe('note.txt: ');
    setSpy.mockRestore();
    expect(FormData.prototype.set).toBe(originalSet);
  });
});

describe('ThreadsPage dream-origin badge accessible names across a locale switch', () => {
  const NAME = { en: 'Dream-originated', 'zh-CN': '源自梦境' } as const;
  const other = (l: 'en' | 'zh-CN') => (l === 'en' ? 'zh-CN' : 'en');

  test.each([
    ['en', 'zh-CN'],
    ['zh-CN', 'en'],
  ] as const)('list row badge: %s -> %s -> back keeps the node and localizes its name', async (from, to) => {
    const threads = [
      mkThread('THR-10', AUTHORED_SUBJECT, { pinned: true, composed_from_dream_id: 'DRM-1' }),
      mkThread('THR-11', 'Unpinned dream', { composed_from_dream_id: 'DRM-2' }),
      mkThread('THR-2', 'Ordinary subject'),
    ];
    stubList(threads);
    mount(`/orgs/${SLUG}/threads`, from);

    const pinnedRow = await screen.findByRole('link', { name: new RegExp('THR-10') });
    const unpinnedRow = screen.getByRole('link', { name: new RegExp('THR-11') });
    const plainRow = screen.getByRole('link', { name: new RegExp('THR-2') });
    const pinnedBadge = within(pinnedRow).getByRole('img', { name: NAME[from] });
    const unpinnedBadge = within(unpinnedRow).getByRole('img', { name: NAME[from] });
    expect(within(plainRow).queryByRole('img')).not.toBeInTheDocument();
    const href = pinnedRow.getAttribute('href');
    const storageBefore = JSON.stringify({ ...localStorage });

    const requests = await countRequests(async () => {
      for (const step of [to, from] as const) {
        await switchLocale(step);
        expect(screen.getByRole('link', { name: new RegExp('THR-10') })).toBe(pinnedRow);
        expect(within(pinnedRow).getByRole('img', { name: NAME[step] })).toBe(pinnedBadge);
        expect(within(unpinnedRow).getByRole('img', { name: NAME[step] })).toBe(unpinnedBadge);
        expect(screen.queryAllByRole('img', { name: NAME[other(step)] })).toHaveLength(0);
        // Authored subject + machine id/href unchanged.
        expect(within(pinnedRow).getByText(AUTHORED_SUBJECT)).toBeInTheDocument();
        expect(pinnedRow).toHaveAttribute('href', href!);
      }
    });
    expect(requests).toEqual([]);
    expect(JSON.stringify({ ...localStorage })).toBe(storageBefore);
    // Row still navigates (behaviour unchanged).
    expect(href).toBe(`/orgs/${SLUG}/threads/THR-10`);
  });

  test.each([
    ['en', 'zh-CN'],
    ['zh-CN', 'en'],
  ] as const)('detail header + rail badges: %s -> %s -> back keeps nodes and localizes names', async (from, to) => {
    const thread = mkThread('THR-5', AUTHORED_SUBJECT, { composed_from_dream_id: 'DRM-9' });
    stubList([]);
    stubDetail(thread, [mkMessage(1, 'founder', AUTHORED_BODY)]);
    const writes: string[] = [];
    server.use(
      http.patch(`/api/v1/orgs/${SLUG}/threads/THR-5`, () => {
        writes.push('patch');
        return HttpResponse.json({});
      }),
    );
    mount(`/orgs/${SLUG}/threads/THR-5`, from);

    await screen.findByText('bold');
    const rail = screen.getByRole('complementary', { name: from === 'en' ? 'Thread properties' : '会话属性' });
    const railBadge = within(rail).getByRole('img', { name: NAME[from] });
    const allBadges = screen.getAllByRole('img', { name: NAME[from] });
    expect(allBadges).toHaveLength(2);
    const headerBadge = allBadges.find((b) => b !== railBadge)!;
    expect(rail.contains(headerBadge)).toBe(false);
    const storageBefore = JSON.stringify({ ...localStorage });

    const requests = await countRequests(async () => {
      for (const step of [to, from] as const) {
        await switchLocale(step);
        const now = screen.getAllByRole('img', { name: NAME[step] });
        expect(now).toHaveLength(2);
        expect(now).toContain(headerBadge);
        expect(now).toContain(railBadge);
        expect(screen.queryAllByRole('img', { name: NAME[other(step)] })).toHaveLength(0);
        expect(screen.getAllByText(AUTHORED_SUBJECT).length).toBeGreaterThan(0);
        expect(within(rail).getByText('THR-5')).toBeInTheDocument();
      }
    });
    expect(requests).toEqual([]);
    expect(writes).toEqual([]);
    expect(JSON.stringify({ ...localStorage })).toBe(storageBefore);
  });
});
