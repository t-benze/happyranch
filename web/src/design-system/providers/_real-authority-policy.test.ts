import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({ useParams: () => ({ slug: 'alpha' }) }));
const teamRoster = vi.hoisted(() => ({ current: [
  { name: 'engineering', manager: 'engineering_manager', workers: [] as string[] },
] }));
vi.mock('@/hooks/teams', () => ({ useTeamsList: () => ({
  data: { teams: teamRoster.current },
  isLoading: false, isError: false,
}) }));
vi.mock('@/lib/api/authorityPolicy', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/authorityPolicy')>(
    '@/lib/api/authorityPolicy',
  );
  return { ...actual, getTeamEscalationPolicy: vi.fn(),
    createTeamEscalationPolicyRelease: vi.fn(), activateTeamEscalationPolicyRelease: vi.fn(),
    createAndActivateTeamEscalationPolicyV2: vi.fn(), activateTeamEscalationPolicyV2: vi.fn(),
    getTeamEscalationPolicyHistory: vi.fn(), getTeamEscalationPolicyV2History: vi.fn(),
    getTeamEscalationPolicyOutcomes: vi.fn() };
});

import * as api from '@/lib/api/authorityPolicy';
import { realAuthorityPolicyApi } from './_real-authority-policy';

const manager = { name: 'engineering_manager', team: 'engineering', role: 'manager' };
const SELECTOR_ID = `APS-${'b'.repeat(64)}`;
const bootstrapTemplate = {
  title: 'Policy', normative_text: 'text', clauses: [],
  continuation_phrase: 'routine same-root follow-through of the already-completed slice',
};
const v2Starter = { policy_id: 'team-8c85b6639e62e10b-dual-text',
  title: 'Engineering escalation policy', what_to_escalate: 'Escalate starter.',
  what_not_to_escalate: 'Continue starter.' };
const empty = {
  team: 'engineering' as const,
  target_manager: 'engineering_manager' as const,
  can_mutate: true as const,
  bootstrap_required: true as const,
  family: 'empty' as const,
  selector_id: SELECTOR_ID,
  selector_epoch: 0 as const,
  bootstrap_template: bootstrapTemplate,
  v2_starter: v2Starter,
};
const active = {
  team: 'engineering' as const,
  target_manager: 'engineering_manager' as const,
  can_mutate: true as const,
  family: 'legacy_v1' as const,
  contract_version: 'v1' as const,
  selector_id: SELECTOR_ID,
  selector_epoch: 2,
  bootstrap_template: bootstrapTemplate,
  v2_starter: v2Starter,
  active: {
    family: 'legacy_v1' as const,
    activation_id: 'APA-1', epoch: 1, action: 'bootstrap' as const,
    created_at: '2026-09-02T00:00:00Z',
    actor_attribution: 'shared local operator credential' as const,
    release: {
      id: 'APR-1', policy_id: 'engineering/pre-escalation-authority', version: 1,
      title: 'Policy', normative_text: 'text', clauses: [],
      continuation_phrase: 'routine same-root follow-through of the already-completed slice',
      digest: 'digest', created_at: '2026-09-02T00:00:00Z',
      actor_attribution: 'shared local operator credential' as const,
    },
  },
};

function setup(agent = manager) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return { client, hook: renderHook(() => realAuthorityPolicyApi.useTeamEscalationPolicy(agent), { wrapper }) };
}

function setupHistory() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return renderHook(() => realAuthorityPolicyApi.useTeamEscalationPolicyHistory(manager), { wrapper });
}

function setupV2History() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return renderHook(() => realAuthorityPolicyApi.useTeamEscalationPolicyV2History(manager), { wrapper });
}

function setupOutcomes() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  return renderHook(() => realAuthorityPolicyApi.useTeamEscalationPolicyOutcomes(manager), { wrapper });
}

function setupMutations() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  const hook = renderHook(() => ({
    create: realAuthorityPolicyApi.useCreateTeamEscalationPolicyRelease(),
    activate: realAuthorityPolicyApi.useActivateTeamEscalationPolicyRelease(),
  }), { wrapper });
  return { client, hook };
}

function setupV2Mutations() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  const hook = renderHook(() => ({
    v2History: realAuthorityPolicyApi.useTeamEscalationPolicyV2History(manager),
    v1History: realAuthorityPolicyApi.useTeamEscalationPolicyHistory(manager),
    outcomes: realAuthorityPolicyApi.useTeamEscalationPolicyOutcomes(manager),
    create: realAuthorityPolicyApi.useCreateTeamEscalationPolicyV2Release(),
    activate: realAuthorityPolicyApi.useActivateTeamEscalationPolicyV2Release(),
  }), { wrapper });
  return { client, hook };
}

function setupHistoryWithMutations() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client }, children);
  const hook = renderHook(() => ({
    history: realAuthorityPolicyApi.useTeamEscalationPolicyHistory(manager),
    outcomes: realAuthorityPolicyApi.useTeamEscalationPolicyOutcomes(manager),
    create: realAuthorityPolicyApi.useCreateTeamEscalationPolicyRelease(),
    activate: realAuthorityPolicyApi.useActivateTeamEscalationPolicyRelease(),
  }), { wrapper });
  return { client, hook };
}

beforeEach(() => {
  vi.clearAllMocks();
  teamRoster.current = [
    { name: 'engineering', manager: 'engineering_manager', workers: [] },
  ];
});

describe('team escalation policy query gate', () => {
  it.each([
    ['create', 'APR-2'],
    ['activate', 'APR-1'],
  ] as const)('refreshes exact policy and history caches after successful %s', async (kind, releaseId) => {
    vi.mocked(api.createTeamEscalationPolicyRelease).mockResolvedValue({ release: { id: releaseId } } as never);
    vi.mocked(api.activateTeamEscalationPolicyRelease).mockResolvedValue({ activation: { id: 'APA-2' } } as never);
    const { client, hook } = setupMutations();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const variables = kind === 'create'
      ? { agentName: 'engineering_manager', body: {} as never }
      : { agentName: 'engineering_manager', body: {} as never };

    await hook.result.current[kind].mutateAsync(variables);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['team-escalation-policy', 'alpha', 'engineering_manager'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['team-escalation-policy-history', 'alpha', 'engineering_manager'] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['team-escalation-policy-outcomes', 'alpha', 'engineering_manager'] });
  });

  it.each([
    ['create', 'v2_create_activate'],
    ['activate', 'v2_activate'],
  ] as const)('a successful v2 %s control invalidates current plus v2 history only', async (kind, control) => {
    vi.mocked(api.createAndActivateTeamEscalationPolicyV2).mockResolvedValue({ control } as never);
    vi.mocked(api.activateTeamEscalationPolicyV2).mockResolvedValue({ control } as never);
    const { client, hook } = setupV2Mutations();
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    await hook.result.current[kind].mutateAsync({ agentName: 'engineering_manager', body: {} as never });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['team-escalation-policy', 'alpha', 'engineering_manager'] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['team-escalation-policy-v2-history', 'alpha', 'engineering_manager'] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['team-escalation-policy-history', 'alpha', 'engineering_manager'] });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ['team-escalation-policy-outcomes', 'alpha', 'engineering_manager'] });
  });

  it('does not invalidate loaded caches when a mutation fails', async () => {
    vi.mocked(api.createTeamEscalationPolicyRelease).mockRejectedValue(new Error('save failed'));
    const { client, hook } = setupMutations();
    client.setQueryData(['team-escalation-policy-history', 'alpha', 'engineering_manager'], { pages: ['loaded'] });
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    await expect(hook.result.current.create.mutateAsync({ agentName: 'engineering_manager', body: {} as never })).rejects.toThrow('save failed');

    expect(invalidate).not.toHaveBeenCalled();
    expect(client.getQueryData(['team-escalation-policy-history', 'alpha', 'engineering_manager'])).toEqual({ pages: ['loaded'] });
  });

  it.each([
    ['create', 'APR-created', {}],
    ['activate', 'APR-activated', { action: 'activate' }],
    ['activate', 'APR-rollback', { action: 'reactivate_rollback' }],
  ] as const)('visibly refreshes history after successful %s mutation for %s', async (kind, releaseId, body) => {
    vi.mocked(api.getTeamEscalationPolicyHistory)
      .mockResolvedValueOnce({ items: [{ release_id: 'APR-old' }] as never, next_cursor: null })
      .mockResolvedValueOnce({ items: [{ release_id: releaseId }, { release_id: 'APR-old' }] as never, next_cursor: null });
    vi.mocked(api.getTeamEscalationPolicyOutcomes)
      .mockResolvedValue({ items: [{ candidate_id: 'AUTH-stable' }] as never, next_cursor: null });
    vi.mocked(api.createTeamEscalationPolicyRelease).mockResolvedValue({ release: { id: releaseId } } as never);
    vi.mocked(api.activateTeamEscalationPolicyRelease).mockResolvedValue({ activation: { id: 'APA-new' } } as never);
    const { hook } = setupHistoryWithMutations();
    await waitFor(() => expect(hook.result.current.history.data?.pages[0].items[0].release_id).toBe('APR-old'));
    await waitFor(() => expect(hook.result.current.outcomes.data?.pages).toHaveLength(1));

    await hook.result.current[kind].mutateAsync({ agentName: 'engineering_manager', body: body as never });

    await waitFor(() => expect(hook.result.current.history.data?.pages[0].items.map((row) => row.release_id)).toEqual([releaseId, 'APR-old']));
    expect(api.getTeamEscalationPolicyHistory).toHaveBeenCalledTimes(2);
    expect(api.getTeamEscalationPolicyOutcomes).toHaveBeenCalledTimes(1);
    expect(hook.result.current.outcomes.data?.pages[0].items.map((row) => row.candidate_id)).toEqual(['AUTH-stable']);
  });

  it.each([
    { name: 'dev_agent', team: 'engineering', role: 'worker' },
    { name: 'content_manager', team: 'content', role: 'manager' },
    { name: 'guessed', team: 'engineering', role: 'manager' },
  ])('creates no request or cache entry for $name', (agent) => {
    const { client, hook } = setup(agent);
    expect(hook.result.current.isLoading).toBe(false);
    expect(api.getTeamEscalationPolicy).not.toHaveBeenCalled();
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });

  it('evicts every exact sensitive cache family when eligibility is lost', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockResolvedValue(empty);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client }, children);
    const view = renderHook(
      ({ agent }) => realAuthorityPolicyApi.useTeamEscalationPolicy(agent),
      { wrapper, initialProps: { agent: manager } },
    );
    await waitFor(() => expect(view.result.current.data).toEqual(empty));
    for (const family of ['team-escalation-policy-history',
      'team-escalation-policy-v2-history', 'team-escalation-policy-outcomes']) {
      client.setQueryData([family, 'alpha', manager.name, manager.team], { sensitive: true });
    }

    teamRoster.current = [];
    view.rerender({ agent: manager });

    await waitFor(() => expect(client.getQueryCache().getAll()).toHaveLength(0));
    expect(api.getTeamEscalationPolicy).toHaveBeenCalledTimes(1);
  });

  it('evicts the prior tuple before sequential navigation and scopes the next cache by team', async () => {
    const contentManager = { name: 'content_manager', team: 'content', role: 'manager' };
    const content = { ...empty, team: 'content', target_manager: 'content_manager',
      bootstrap_template: null, v2_starter: { ...v2Starter,
        policy_id: 'team-ed7002b439e9ac84-dual-text', title: 'Content escalation policy' } };
    vi.mocked(api.getTeamEscalationPolicy)
      .mockResolvedValueOnce(empty)
      .mockResolvedValueOnce(content);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) =>
      React.createElement(QueryClientProvider, { client }, children);
    const view = renderHook(
      ({ agent }) => realAuthorityPolicyApi.useTeamEscalationPolicy(agent),
      { wrapper, initialProps: { agent: manager } },
    );
    await waitFor(() => expect(view.result.current.data).toEqual(empty));
    teamRoster.current = [{ name: 'content', manager: 'content_manager', workers: [] }];

    view.rerender({ agent: contentManager });

    await waitFor(() => expect(view.result.current.data).toEqual(content));
    expect(client.getQueryData([
      'team-escalation-policy', 'alpha', manager.name, manager.team,
    ])).toBeUndefined();
    expect(client.getQueryData([
      'team-escalation-policy', 'alpha', contentManager.name, contentManager.team,
    ])).toEqual(content);
    expect(api.getTeamEscalationPolicy).toHaveBeenNthCalledWith(2, 'alpha', 'content_manager');
  });

  it('exposes loading then active release-creation state for the eligible tuple', async () => {
    let resolve!: (value: typeof active) => void;
    vi.mocked(api.getTeamEscalationPolicy).mockReturnValue(
      new Promise((done) => { resolve = done; }),
    );
    const { hook } = setup();
    expect(hook.result.current.isLoading).toBe(true);
    resolve(active);
    await waitFor(() => expect(hook.result.current.data).toEqual(active));
    expect(hook.result.current.data?.can_mutate).toBe(true);
  });

  it('exposes the empty release-creation state for the eligible tuple', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockResolvedValue(empty);
    const { hook } = setup();
    await waitFor(() => expect(hook.result.current.data).toEqual(empty));
    expect(hook.result.current.data?.can_mutate).toBe(true);
  });

  it('exposes sanitized query errors', async () => {
    vi.mocked(api.getTeamEscalationPolicy).mockRejectedValue(new Error('unavailable'));
    const { hook } = setup();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.error).toBeInstanceOf(Error);
  });

  it('uses the server cursor to reach page two exactly once', async () => {
    vi.mocked(api.getTeamEscalationPolicyHistory)
      .mockResolvedValueOnce({ items: [{ release_id: 'APR-2' }] as never, next_cursor: 'history-cursor' })
      .mockResolvedValueOnce({ items: [{ release_id: 'APR-1' }] as never, next_cursor: null });
    const hook = setupHistory();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(1));
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(2));
    expect(api.getTeamEscalationPolicyHistory).toHaveBeenNthCalledWith(1, 'alpha', 'engineering_manager', undefined);
    expect(api.getTeamEscalationPolicyHistory).toHaveBeenNthCalledWith(2, 'alpha', 'engineering_manager', 'history-cursor');
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.release_id)).toEqual(['APR-2', 'APR-1']);
    expect(hook.result.current.hasNextPage).toBe(false);
  });

  it('keeps v2 history pagination on its own family cursor stream', async () => {
    vi.mocked(api.getTeamEscalationPolicyV2History)
      .mockResolvedValueOnce({ items: [{ release_id: 'APV2-2' }] as never, next_cursor: 'v2-cursor' })
      .mockResolvedValueOnce({ items: [{ release_id: 'APV2-1' }] as never, next_cursor: null });
    const hook = setupV2History();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(1));
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(2));
    expect(api.getTeamEscalationPolicyV2History).toHaveBeenNthCalledWith(1, 'alpha', 'engineering_manager', undefined);
    expect(api.getTeamEscalationPolicyV2History).toHaveBeenNthCalledWith(2, 'alpha', 'engineering_manager', 'v2-cursor');
    expect(api.getTeamEscalationPolicyHistory).not.toHaveBeenCalled();
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.release_id)).toEqual(['APV2-2', 'APV2-1']);
  });

  it('retries the initial v2 history page without consulting legacy history', async () => {
    vi.mocked(api.getTeamEscalationPolicyV2History)
      .mockRejectedValueOnce(new Error('v2 history unavailable'))
      .mockResolvedValueOnce({ items: [{ release_id: 'APV2-1' }] as never, next_cursor: null });
    const hook = setupV2History();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    await hook.result.current.refetch();
    await waitFor(() => expect(hook.result.current.data?.pages[0].items[0].release_id).toBe('APV2-1'));
    expect(api.getTeamEscalationPolicyV2History).toHaveBeenCalledTimes(2);
    expect(api.getTeamEscalationPolicyHistory).not.toHaveBeenCalled();
  });

  it('preserves history page one across cursor failure and native retry appends page two once', async () => {
    vi.mocked(api.getTeamEscalationPolicyHistory)
      .mockResolvedValueOnce({ items: [{ release_id: 'APR-2' }] as never, next_cursor: 'history-cursor' })
      .mockRejectedValueOnce(new Error('page two unavailable'))
      .mockResolvedValueOnce({ items: [{ release_id: 'APR-1' }] as never, next_cursor: null });
    const hook = setupHistory();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(1));
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.release_id)).toEqual(['APR-2']);
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(2));
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.release_id)).toEqual(['APR-2', 'APR-1']);
    expect(api.getTeamEscalationPolicyHistory).toHaveBeenNthCalledWith(2, 'alpha', 'engineering_manager', 'history-cursor');
    expect(api.getTeamEscalationPolicyHistory).toHaveBeenNthCalledWith(3, 'alpha', 'engineering_manager', 'history-cursor');
    expect(hook.result.current.hasNextPage).toBe(false);
  });

  it('preserves outcomes page one across cursor failure and native retry appends page two once', async () => {
    vi.mocked(api.getTeamEscalationPolicyOutcomes)
      .mockResolvedValueOnce({ items: [{ candidate_id: 'AUTH-2' }] as never, next_cursor: 'outcome-cursor' })
      .mockRejectedValueOnce(new Error('page two unavailable'))
      .mockResolvedValueOnce({ items: [{ candidate_id: 'AUTH-1' }] as never, next_cursor: null });
    const hook = setupOutcomes();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(1));
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.isError).toBe(true));
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.candidate_id)).toEqual(['AUTH-2']);
    await hook.result.current.fetchNextPage();
    await waitFor(() => expect(hook.result.current.data?.pages).toHaveLength(2));
    expect(hook.result.current.data?.pages.flatMap((page) => page.items).map((row) => row.candidate_id)).toEqual(['AUTH-2', 'AUTH-1']);
    expect(api.getTeamEscalationPolicyOutcomes).toHaveBeenNthCalledWith(2, 'alpha', 'engineering_manager', 'outcome-cursor');
    expect(api.getTeamEscalationPolicyOutcomes).toHaveBeenNthCalledWith(3, 'alpha', 'engineering_manager', 'outcome-cursor');
    expect(hook.result.current.hasNextPage).toBe(false);
  });
});
