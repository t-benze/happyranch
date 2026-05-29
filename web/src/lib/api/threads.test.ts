import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import {
  abandonThread,
  archiveThread,
  composeThread,
  extendThreadCap,
  getThread,
  inviteToThread,
  listThreadMessages,
  listThreads,
  sendThreadFollowUp,
  threadInboxEventsPath,
  threadTailPath,
} from './threads';

const SLUG = 'alpha';
const seedToken = () => sessionStorage.setItem('grassland.token', 'tok');

describe('threads api mirror', () => {
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
          new_kb_slugs: null,
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

  test.each([
    ['sendThreadFollowUp', () => sendThreadFollowUp(SLUG, 'THR-001', { body_markdown: 'x' }), '/send'],
    ['inviteToThread', () => inviteToThread(SLUG, 'THR-001', { agent_name: 'a' }), '/invite'],
    ['extendThreadCap', () => extendThreadCap(SLUG, 'THR-001', { new_cap: 999 }), '/extend'],
    ['archiveThread', () => archiveThread(SLUG, 'THR-001', { summary: 'done' }), '/archive'],
    ['abandonThread', () => abandonThread(SLUG, 'THR-001', { reason: 'bye' }), '/abandon'],
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

  test('SSE path helpers return stable strings', () => {
    expect(threadInboxEventsPath(SLUG)).toBe(`/orgs/${SLUG}/threads/events`);
    expect(threadTailPath(SLUG, 'THR-001', 5)).toEqual({
      path: `/orgs/${SLUG}/threads/THR-001/tail`,
      query: { since_seq: 5 },
    });
  });
});
