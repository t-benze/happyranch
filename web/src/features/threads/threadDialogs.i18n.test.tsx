/**
 * THR-118 W3a — Archive / Invite / RemoveParticipant dialogs, the reply
 * delivery + responder strips and ResumeButton under the real I18nProvider.
 *
 * Dialogs: a locale switch in either direction keeps the dialog open, the same
 * field node/value and focus, and issues zero requests; the confirm then posts
 * exactly once. A mapped daemon error re-translates in place without a resubmit
 * while a raw diagnostic (`HTTP 500`, empty) stays byte-exact.
 * Strips/Resume: zh-CN product labels, with agent names, sequence numbers and
 * raw machine values (rc, unknown category) byte-identical.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { useState, type ReactElement } from 'react';
import { Route, Routes } from 'react-router-dom';
import { OrgProvider } from '@/lib/orgSlug';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import type { ReplyDeliveryEntry, ResponderStatusEntry } from '@/lib/api/types';
import { ArchiveDialog } from './ArchiveDialog';
import { InviteDialog } from './InviteDialog';
import { RemoveParticipantDialog } from './RemoveParticipantDialog';
import { ReplyDeliveryStrip } from './ReplyDeliveryStrip';
import { ResponderStatusStrip } from './ResponderStatusStrip';
import { ResumeButton } from './ResumeButton';

const SLUG = 'alpha';
const THREAD = 'THR-7';

function mount(ui: (onClose: () => void) => ReactElement, locale: 'en' | 'zh-CN' = 'en') {
  function Harness() {
    const [closed, setClosed] = useState(0);
    return (
      <>
        {ui(() => setClosed((n) => n + 1))}
        <span data-testid="closed">{closed}</span>
        <LocaleTestSwitch to="zh-CN" />
        <LocaleTestSwitch to="en" />
      </>
    );
  }
  return renderWithProviders(
    <Routes>
      <Route
        path="/orgs/:slug/*"
        element={
          <OrgProvider>
            <Harness />
          </OrgProvider>
        }
      />
    </Routes>,
    { route: `/orgs/${SLUG}/threads/${THREAD}`, i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

function recordRequests(): string[] {
  const ledger: string[] = [];
  server.events.on('request:start', ({ request }) => {
    ledger.push(`${request.method} ${new URL(request.url).pathname}`);
  });
  return ledger;
}

async function switchTo(locale: 'en' | 'zh-CN') {
  act(() => screen.getByTestId(`test-set-locale-${locale}`).click());
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

type Reply = 'mapped' | 'unmapped' | 'ok';

/** One POST endpoint whose next reply is scripted; counts every POST. */
function scriptEndpoint(path: string, mappedCode: string) {
  const state = { reply: 'mapped' as Reply, posts: [] as unknown[] };
  server.use(
    http.post(`/api/v1/orgs/${SLUG}/threads/${THREAD}/${path}`, async ({ request }) => {
      const text = await request.text();
      state.posts.push(text ? JSON.parse(text) : null);
      if (state.reply === 'mapped') {
        return HttpResponse.json({ detail: { code: mappedCode } }, { status: 400 });
      }
      if (state.reply === 'unmapped') {
        return HttpResponse.json({ detail: { code: 'not_in_the_map' } }, { status: 500 });
      }
      return HttpResponse.json({ ok: true });
    }),
  );
  return state;
}

/** Drive mapped → unmapped → empty-raw → success through one confirm button. */
async function exerciseErrors(opts: {
  user: ReturnType<typeof userEvent.setup>;
  state: { reply: Reply; posts: unknown[] };
  confirmEn: string;
  mappedEn: string;
  mappedZh: string;
  findError: () => HTMLElement | null;
}) {
  const { user, state, confirmEn, mappedEn, mappedZh, findError } = opts;
  const confirm = () => screen.getByRole('button', { name: confirmEn });

  state.reply = 'mapped';
  await user.click(confirm());
  await waitFor(() => expect(findError()?.textContent).toBe(mappedEn));
  const mapped = findError()!;
  expect(state.posts).toHaveLength(1);
  await switchTo('zh-CN');
  expect(findError()).toBe(mapped);
  expect(mapped.textContent).toBe(mappedZh);
  await switchTo('en');
  expect(mapped.textContent).toBe(mappedEn);
  expect(state.posts).toHaveLength(1);

  state.reply = 'unmapped';
  await user.click(confirm());
  await waitFor(() => expect(findError()?.textContent).toBe('HTTP 500'));
  expect(state.posts).toHaveLength(2);
  await switchTo('zh-CN');
  expect(findError()?.textContent).toBe('HTTP 500');
  await switchTo('en');
  expect(findError()?.textContent).toBe('HTTP 500');
  expect(state.posts).toHaveLength(2);

  // Empty raw diagnostic: a non-Error rejection of '' still renders its node.
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => Promise.reject(''));
  await user.click(confirm());
  await waitFor(() => expect(findError()?.textContent).toBe(''));
  expect(findError()).not.toBeNull();
  await switchTo('zh-CN');
  expect(findError()).not.toBeNull();
  expect(findError()?.textContent).toBe('');
  await switchTo('en');
  fetchSpy.mockRestore();
  expect(state.posts).toHaveLength(2);

  state.reply = 'ok';
  await user.click(confirm());
  await waitFor(() => expect(screen.getByTestId('closed')).toHaveTextContent('1'));
  expect(state.posts).toHaveLength(3);
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok');
});

afterEach(() => {
  server.events.removeAllListeners();
  vi.restoreAllMocks();
});

describe('ArchiveDialog i18n', () => {
  test('switch preserves summary/focus/open with no request; errors relocalize; confirm posts once', async () => {
    const user = userEvent.setup();
    const state = scriptEndpoint('archive', 'thread_not_open');
    mount((onClose) => <ArchiveDialog threadId={THREAD} open onClose={onClose} />);
    expect(screen.getByRole('dialog', { name: 'Archive thread' })).toBeInTheDocument();
    const summary = screen.getByLabelText(
      'Founder summary (optional, will be saved to transcript)',
    ) as HTMLTextAreaElement;
    await user.type(summary, 'Wrapped up **Q3**');
    expect(summary).toHaveFocus();

    const ledger = recordRequests();
    await switchTo('zh-CN');
    expect(screen.getByRole('dialog', { name: '归档会话' })).toBeInTheDocument();
    expect(screen.getByLabelText('创始人摘要（可选，将保存到会话记录中）')).toBe(summary);
    expect(summary.value).toBe('Wrapped up **Q3**');
    expect(summary).toHaveFocus();
    expect(screen.getByRole('button', { name: '归档' })).toBeInTheDocument();
    await switchTo('en');
    expect(screen.getByRole('dialog', { name: 'Archive thread' })).toBeInTheDocument();
    expect(summary.value).toBe('Wrapped up **Q3**');
    expect(summary).toHaveFocus();
    expect(ledger).toEqual([]);

    await exerciseErrors({
      user,
      state,
      confirmEn: 'Archive',
      mappedEn: 'This thread is no longer open.',
      mappedZh: '此会话已不再开放。',
      findError: () => screen.queryByRole('alert'),
    });
    expect(state.posts.at(-1)).toEqual({ summary: 'Wrapped up **Q3**' });
  });
});

describe('InviteDialog i18n', () => {
  test('switch preserves recipients/focus/open; validation + errors relocalize; confirm posts once', async () => {
    const user = userEvent.setup();
    const state = scriptEndpoint('invite', 'unknown_agent');
    mount((onClose) => <InviteDialog threadId={THREAD} open onClose={onClose} agents={[]} />);

    // Mapped client-side validation re-translates without a request.
    await user.click(screen.getByRole('button', { name: 'Invite' }));
    const required = screen.getByRole('alert');
    expect(required.textContent).toBe('Agent name is required.');
    const ledger = recordRequests();
    await switchTo('zh-CN');
    expect(screen.getByRole('alert')).toBe(required);
    expect(required.textContent).toBe('智能体名称为必填项。');
    await switchTo('en');
    expect(required.textContent).toBe('Agent name is required.');

    const input = screen.getByLabelText('Agent name') as HTMLInputElement;
    await user.type(input, 'ops_lead');
    expect(input).toHaveFocus();
    await switchTo('zh-CN');
    expect(screen.getByRole('dialog', { name: '邀请参与者' })).toBeInTheDocument();
    expect(screen.getByLabelText('智能体名称')).toBe(input);
    expect(input.value).toBe('ops_lead');
    expect(input).toHaveFocus();
    await switchTo('en');
    expect(input.value).toBe('ops_lead');
    expect(input).toHaveFocus();
    expect(ledger).toEqual([]);

    await exerciseErrors({
      user,
      state,
      confirmEn: 'Invite',
      mappedEn: "That agent doesn't exist in this org.",
      mappedZh: '该智能体在此组织中不存在。',
      findError: () => screen.queryByRole('alert'),
    });
    expect(state.posts.at(-1)).toEqual({ agent_name: 'ops_lead' });
  });
});

describe('RemoveParticipantDialog i18n', () => {
  test('agent name stays exact; switch preserves focus/open; errors relocalize; confirm posts once', async () => {
    const user = userEvent.setup();
    const state = scriptEndpoint('remove-participant', 'not_participant');
    mount((onClose) => (
      <RemoveParticipantDialog threadId={THREAD} agentName="ops_lead" open onClose={onClose} />
    ));
    const confirm = screen.getByRole('button', { name: 'Remove' });
    confirm.focus();
    const ledger = recordRequests();
    await switchTo('zh-CN');
    const dialog = screen.getByRole('dialog', { name: '移除参与者' });
    expect(dialog).toHaveTextContent(
      '要将 ops_lead 从此会话中移除吗？对方将不再收到消息，所有待处理的回复都会被取消。',
    );
    expect(screen.getByText('ops_lead')).toHaveClass('font-semibold');
    expect(screen.getByRole('button', { name: '移除' })).toBe(confirm);
    expect(confirm).toHaveFocus();
    await switchTo('en');
    expect(screen.getByRole('button', { name: 'Remove' })).toBe(confirm);
    expect(confirm).toHaveFocus();
    expect(ledger).toEqual([]);

    const errorP = () =>
      document.querySelector<HTMLElement>('[role="dialog"] p.text-feedback-danger');
    await exerciseErrors({
      user,
      state,
      confirmEn: 'Remove',
      mappedEn: "That agent isn't a participant in this thread.",
      mappedZh: '该智能体不是此会话的参与者。',
      findError: errorP,
    });
    expect(state.posts.at(-1)).toEqual({ agent_name: 'ops_lead' });
  });
});

describe('strips + ResumeButton in zh-CN', () => {
  const nowMs = Date.parse('2026-05-13T17:46:00Z');
  function delivery(over: Partial<ReplyDeliveryEntry>): ReplyDeliveryEntry {
    return {
      agent_name: 'ops_lead',
      state: 'queued',
      from_seq: 1,
      through_seq: 4,
      coalesced_message_count: 4,
      started_at: null,
      updated_at: '2026-05-13T17:45:00Z',
      last_terminal_reason: null,
      current_failure_category: null,
      ...over,
    };
  }
  function responder(
    agent_name: string,
    status: ResponderStatusEntry['status'],
    over: Partial<ResponderStatusEntry> = {},
  ): ResponderStatusEntry {
    return {
      agent_name,
      purpose: 'reply',
      status,
      responded_at: null,
      started_at: null,
      decline_reason: null,
      category: null,
      ...over,
    };
  }

  test('ReplyDeliveryStrip localizes captions/aria; names, seqs and unknown category stay raw', async () => {
    mount(
      () => (
        <ReplyDeliveryStrip
          nowMs={nowMs}
          entries={[
            delivery({ agent_name: 'frontend_engineer', state: 'running', from_seq: 1, through_seq: 3, started_at: '2026-05-13T17:45:00Z' }),
            delivery({ agent_name: 'qa_engineer', state: 'queued', coalesced_message_count: 3 }),
            delivery({ agent_name: 'consultant_head', state: 'held', from_seq: 9, through_seq: 9 }),
            delivery({ agent_name: 'support_lead', state: 'retry_required', from_seq: 2, through_seq: 5, current_failure_category: 'infra_fail', last_terminal_reason: 'RAW DETAIL' }),
            delivery({ agent_name: 'ops_x', state: 'retry_required', // A future daemon category outside today's closed union stays raw.
              current_failure_category: 'brand_new_cause' as unknown as ReplyDeliveryEntry['current_failure_category'] }),
          ]}
        />
      ),
      'zh-CN',
    );
    expect(screen.getByText('当前有 5 个投递')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: '进行中的回复投递' })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '1 个排队中的投递' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: '等待中的回复投递' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: '回复投递诊断' })).toBeInTheDocument();
    expect(screen.getByText('正在回复 1 分钟 · 消息 1–3')).toBeInTheDocument();
    expect(screen.getByText('已合并 3 条消息 · 消息 1–4')).toBeInTheDocument();
    expect(screen.getByText('等待当前交流结束 · 消息 9')).toBeInTheDocument();
    expect(screen.getByText('需要重试 · 消息 2–5 · 基础设施故障')).toBeInTheDocument();
    expect(screen.getByText('需要重试 · 消息 1–4 · brand new cause')).toBeInTheDocument();
    for (const name of ['frontend_engineer', 'qa_engineer', 'consultant_head', 'support_lead', 'ops_x']) {
      expect(screen.getByText(name).textContent).toBe(name);
    }
    expect(screen.queryByText(/RAW DETAIL/)).not.toBeInTheDocument();

    await switchTo('en');
    expect(screen.getByText('replying 1m · messages 1–3')).toBeInTheDocument();
    expect(screen.getByText('retry required · messages 1–4 · brand new cause')).toBeInTheDocument();
  });

  test('ResponderStatusStrip localizes labels; agent names and rc stay raw', () => {
    mount(
      () => (
        <ResponderStatusStrip
          nowMs={nowMs}
          statuses={[
            responder('alpha_agent', 'replied'),
            responder('beta_agent', 'declined', { category: 'declined' }),
            responder('gamma_agent', 'failed', { category: 'infra_fail', decline_reason: 'infra rc=137' }),
            responder('delta_agent', 'failed', { category: 'no_callback' }),
            responder('eps_agent', 'failed', { decline_reason: 'founder_aborted', category: 'infra_fail' }),
            responder('zeta_agent', 'failed'),
          ]}
        />
      ),
      'zh-CN',
    );
    expect(screen.getByText('已回复')).toBeInTheDocument();
    expect(screen.getByText('已拒绝')).toBeInTheDocument();
    expect(screen.getByText('回复失败（基础设施：rc=137）')).toBeInTheDocument();
    expect(screen.getByText('回复失败（无回调）')).toBeInTheDocument();
    expect(screen.getByText('已中止')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    for (const name of ['alpha_agent', 'beta_agent', 'gamma_agent', 'delta_agent', 'eps_agent', 'zeta_agent']) {
      expect(screen.getByText(name).textContent).toBe(name);
    }
  });

  test('ResumeButton localizes label/title; switch keeps node, no request; click posts once', async () => {
    const user = userEvent.setup();
    const resumes: number[] = [];
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/${THREAD}/resume`, () => {
        resumes.push(1);
        return HttpResponse.json({ ok: true });
      }),
    );
    mount(() => <ResumeButton threadId={THREAD} />, 'zh-CN');
    const button = screen.getByRole('button', { name: '恢复会话' });
    expect(button).toHaveAttribute('title', '恢复会话');
    const ledger = recordRequests();
    await switchTo('en');
    expect(screen.getByRole('button', { name: 'Resume thread' })).toBe(button);
    expect(button).toHaveAttribute('title', 'Resume thread');
    await switchTo('zh-CN');
    expect(ledger).toEqual([]);
    await user.click(button);
    await waitFor(() => expect(resumes).toHaveLength(1));
  });
});
