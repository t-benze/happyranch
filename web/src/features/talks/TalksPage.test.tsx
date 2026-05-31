import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'hk-macau-tourism';

const OPEN_TALK = {
  talk_id: 'TALK-0001',
  agent_name: 'engineering_head',
  status: 'open',
  started_at: '2026-05-18T10:00:00Z',
  ended_at: null,
  summary: null,
  topic_list: [],
  new_learnings_count: 0,
  new_kb_slugs: [],
  transcript_path: null,
};

const CLOSED_TALK = {
  ...OPEN_TALK,
  talk_id: 'TALK-0002',
  status: 'closed',
  ended_at: '2026-05-18T11:00:00Z',
  summary: 'Discussed visa updates.',
  topic_list: ['visa'],
  new_learnings_count: 1,
  new_kb_slugs: [],
  transcript_path: '/runtime/orgs/hk-macau-tourism/talks/TALK-0002.md',
  transcript: '## founder\nQ: visa?\n## agent\nA: checked daily.',
};

function stubBaseHandlers() {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/agents`, () =>
      HttpResponse.json({
        agents: [{ name: 'engineering_head', team: 'engineering', role: 'manager' }],
      }),
    ),
  );
}

describe('TalksPage', () => {
  test('renders the inbox and an empty pane when no talk is selected', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/talks`, () =>
        HttpResponse.json({ talks: [OPEN_TALK, CLOSED_TALK] }),
      ),
    );
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/talks` });
    await waitFor(() => {
      expect(screen.getByText(/TALK-0001/)).toBeInTheDocument();
    });
    expect(
      screen.getByRole('heading', { name: /Select a talk/i }),
    ).toBeInTheDocument();
  });

  test('renders an open talk detail with empty transcript', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/talks`, () =>
        HttpResponse.json({ talks: [OPEN_TALK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`, () =>
        HttpResponse.json(OPEN_TALK),
      ),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/talks/${OPEN_TALK.talk_id}`,
    });
    await waitFor(() => {
      expect(screen.getByText(/Talk is open/i)).toBeInTheDocument();
    });
  });

  test('renders a closed talk transcript with both speakers', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    stubBaseHandlers();
    server.use(
      http.get(`/api/v1/orgs/${SLUG}/talks`, () =>
        HttpResponse.json({ talks: [CLOSED_TALK] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/talks/${CLOSED_TALK.talk_id}`, () =>
        HttpResponse.json(CLOSED_TALK),
      ),
    );
    renderWithProviders(<AppRoutes />, {
      route: `/orgs/${SLUG}/talks/${CLOSED_TALK.talk_id}`,
    });
    await waitFor(() => {
      expect(screen.getByText(/Q: visa\?/)).toBeInTheDocument();
    });
    expect(screen.getByText(/A: checked daily\./)).toBeInTheDocument();
  });
});
