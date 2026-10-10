import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import { transferableAbortController } from 'node:util';
import {
  abortReplies,
  archiveThread,
  composeThread,
  extendThreadCap,
  getThread,
  inviteToThread,
  listThreadMessages,
  listThreads,
  removeParticipantFromThread,
  renameThread,
  sendThreadFollowUp,
  setThreadPinned,
  threadInboxEventsPath,
  threadTailPath,
} from './threads';

const SLUG = 'alpha';
const seedToken = () => sessionStorage.setItem('happyranch.token', 'tok');

describe('threads api mirror', () => {
  test('listThreads page overload preserves metadata and sends the opaque continuation without limit', async () => {
    seedToken();
    let query: URLSearchParams | undefined;
    const page = { threads: [], totals: { open: 53, archived: 8, all: 61, dream_origin: 9 },
      has_more: false, next_cursor: null, sampled_at: '2026-10-07T00:00:00Z' };
    server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request }) => {
      query = new URL(request.url).searchParams;
      return HttpResponse.json(page);
    }));
    const response = await listThreads(SLUG, { status: 'archived', page_size: 50, cursor: 'opaque/+ hint' });
    expect(response).toEqual(page);
    expect(Object.fromEntries(query!)).toEqual({ status: 'archived', page_size: '50', cursor: 'opaque/+ hint' });
  });

  test('listThreads forwards cancellation to the actual transport', async () => {
    seedToken();
    const controller = transferableAbortController();
    let signal: AbortSignal | undefined;
    let release!: () => void;
    let entered!: () => void;
    const ready = new Promise<void>((resolve) => { entered = resolve; });
    const responseGate = new Promise<void>((resolve) => { release = resolve; });
    server.use(http.get(`/api/v1/orgs/${SLUG}/threads`, async ({ request }) => {
      signal = request.signal;
      entered();
      await responseGate;
      return HttpResponse.json({ threads: [] });
    }));
    const pending = listThreads(SLUG, { page_size: 50 }, controller.signal);
    const cancelled = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    await ready;
    controller.abort();
    expect(signal?.aborted).toBe(true);
    release();
    await cancelled;
  });
  test('composeThread POSTs the right body', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads`, async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json(
          { thread_id: 'THR-001', started_at: 't', pending_replies: 2 },
          { status: 201 },
        );
      }),
    );
    const r = await composeThread(SLUG, {
      subject: 's',
      recipients: ['a', 'b'],
      body_markdown: 'hi',
    });
    expect(r.thread_id).toBe('THR-001');
    expect(received).toEqual({ subject: 's', recipients: ['a', 'b'], body_markdown: 'hi' });
  });

  test('listThreads passes query params', async () => {
    seedToken();
    let url: string | null = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads`, ({ request: req }) => {
        url = req.url;
        return HttpResponse.json({ threads: [] });
      }),
    );
    await listThreads(SLUG, { status: 'open', limit: 25 });
    expect(url).toMatch(/status=open/);
    expect(url).toMatch(/limit=25/);
  });

  test('getThread returns participants + messages', async () => {
    seedToken();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
        HttpResponse.json({
          thread_id: 'THR-001',
          subject: 's',
          status: 'open',
          started_at: 't',
          archived_at: null,
          forwarded_from_id: null,
          forwarded_from_kind: null,
          turn_cap: 500,
          turns_used: 0,
          summary: null,
          transcript_path: null,
          participants: ['a'],
          messages: [],
        }),
      ),
    );
    const r = await getThread(SLUG, 'THR-001');
    expect(r.participants).toEqual(['a']);
    expect(r.messages).toEqual([]);
  });

  test('listThreadMessages passes since_seq', async () => {
    seedToken();
    let url: string | null = null;
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, ({ request: req }) => {
        url = req.url;
        return HttpResponse.json({ messages: [] });
      }),
    );
    await listThreadMessages(SLUG, 'THR-001', { since_seq: 7 });
    expect(url).toMatch(/since_seq=7/);
  });

  test('sendThreadFollowUp can include attachments', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
      }),
    );

    await sendThreadFollowUp(SLUG, 'THR-001', {
      body_markdown: '',
      attachments: [
        {
          artifact_name: 'THR-001-report.pdf',
          display_name: 'report.pdf',
          content_type: 'application/pdf',
        },
      ],
    });

    expect(received).toEqual({
      body_markdown: '',
      attachments: [
        {
          artifact_name: 'THR-001-report.pdf',
          display_name: 'report.pdf',
          content_type: 'application/pdf',
        },
      ],
    });
  });

  test.each([
    ['sendThreadFollowUp', () => sendThreadFollowUp(SLUG, 'THR-001', { body_markdown: 'x' }), '/send'],
    ['inviteToThread', () => inviteToThread(SLUG, 'THR-001', { agent_name: 'a' }), '/invite'],
    ['removeParticipantFromThread', () => removeParticipantFromThread(SLUG, 'THR-001', { agent_name: 'a' }), '/remove-participant'],
    ['extendThreadCap', () => extendThreadCap(SLUG, 'THR-001', { new_cap: 999 }), '/extend'],
    ['archiveThread', () => archiveThread(SLUG, 'THR-001', { summary: 'done' }), '/archive'],
    ['abortReplies', () => abortReplies(SLUG, 'THR-001'), '/abort-replies'],
  ])('%s hits the correct path', async (_name, call, suffix) => {
    seedToken();
    let hit = false;
    server.use(
      http.post(
        `/api/v1/orgs/${SLUG}/threads/THR-001${suffix}`,
        () => {
          hit = true;
          return HttpResponse.json({ thread_id: 'THR-001' });
        },
      ),
    );
    await call();
    expect(hit).toBe(true);
  });

  test('removeParticipantFromThread POSTs the right body', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/remove-participant`, async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json(
          { thread_id: 'THR-001', agent_name: 'qa_engineer', system_message_seq: 3 },
          { status: 200 },
        );
      }),
    );
    const r = await removeParticipantFromThread(SLUG, 'THR-001', { agent_name: 'qa_engineer' });
    expect(r.thread_id).toBe('THR-001');
    expect(r.agent_name).toBe('qa_engineer');
    expect(r.system_message_seq).toBe(3);
    expect(received).toEqual({ agent_name: 'qa_engineer' });
  });

  test('renameThread POSTs the trimmed subject to /rename (THR-209)', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/rename`, async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001', subject: 'New title' });
      }),
    );
    const r = await renameThread(SLUG, 'THR-001', { subject: '  New title  ' });
    expect(r.subject).toBe('New title');
    expect(received).toEqual({ subject: '  New title  ' });
  });

  test('setThreadPinned POSTs the boolean state to /pin (THR-209)', async () => {
    seedToken();
    let received: unknown = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/pin`, async ({ request: req }) => {
        received = await req.json();
        return HttpResponse.json({ thread_id: 'THR-001', pinned: true });
      }),
    );
    const r = await setThreadPinned(SLUG, 'THR-001', { pinned: true });
    expect(r.pinned).toBe(true);
    expect(received).toEqual({ pinned: true });
  });

  test('SSE path helpers return stable strings', () => {
    expect(threadInboxEventsPath(SLUG)).toBe(`/orgs/${SLUG}/threads/events`);
    expect(threadTailPath(SLUG, 'THR-001', 5)).toEqual({
      path: `/orgs/${SLUG}/threads/THR-001/tail`,
      query: { since_seq: 5 },
    });
  });
});
