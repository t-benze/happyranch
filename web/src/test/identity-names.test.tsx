/** Naming v1 scenarios 2/3/4/5/8/10, shipping pages with MSW HTTP fixtures.
 * Mock-based UI evidence, never daemon/DB E2E proof; execution receipts live in the maker handoff. */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterAll, beforeAll, describe, expect, test, vi } from 'vitest';
import { transferableAbortController } from 'node:util';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { I18nProvider } from '@/hooks/i18n';
import { LocaleTestSwitch, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { OrgProvider } from '@/lib/orgSlug';
import { AgentsPage } from '@/features/agents/AgentsPage';
import { SettingsPage } from '@/features/settings/SettingsPage';
import { ThreadsPage } from '@/features/threads/ThreadsPage';
import { TasksPage } from '@/features/tasks/TasksPage';
import { SkillDetailPage } from '@/features/skills/SkillDetailPage';
import type { IdentityView, RenameBody, ResolveBody } from '@/lib/api/types';
import { translate } from '@/lib/i18n';

// Node fetch requires its own AbortSignal realm; keep real query cancellation.
beforeAll(() => vi.stubGlobal('AbortController', transferableAbortController().constructor));
afterAll(() => vi.unstubAllGlobals());

const t = (key: Parameters<typeof translate>[1]) => translate('en', key);
const orgSettings = {
  session_timeout_seconds: null, reviewer_agents: [],
  dreaming: { enabled: true, schedule: { time: '09:00', timezone: 'UTC' }, catch_up_on_startup: false, agents: { mode: 'all', include: [], exclude: [] } },
  threads: { enabled: true, default_turn_cap: 500, invocation_timeout_seconds: null },
  working_hours: { enabled: true, agents: { mode: 'all', include: [], exclude: [] }, default: { mode: 'windowed', window: { start: '09:00', end: '17:00', timezone: 'UTC' }, interval: '2h', days: ['mon'], catch_up_on_startup: false }, teams: {}, overrides: {} },
};
function fixture() {
  const records: Record<string, IdentityView[]> = Object.fromEntries(['alpha', 'beta'].map((slug) => [slug, [
    { canonical_id: 'agent_a', kind: 'agent', lifecycle: 'active', addressable_name: slug === 'alpha' ? 'Alpha' : 'BetaAlpha', name_revision: 1, canonical_definition_revision: 'a'.repeat(64), naming_status: 'ready' },
    { canonical_id: 'agent_b', kind: 'agent', lifecycle: 'active', addressable_name: 'Other', name_revision: 1, canonical_definition_revision: 'b'.repeat(64), naming_status: 'ready' },
    { canonical_id: 'founder', kind: 'founder', lifecycle: 'founder', addressable_name: slug === 'alpha' ? 'Boss' : 'BetaBoss', name_revision: 1, canonical_definition_revision: null, naming_status: 'ready' },
  ]]));
  const former: Record<string, Record<string, string>> = { alpha: { oldalpha: 'agent_a' }, beta: {} };
  const puts: { slug: string; id: string; body: RenameBody }[] = [];
  const resolves: { slug: string; body: ResolveBody }[] = [];
  const effects: { kind: string; slug: string; body: unknown }[] = [];
  const filters: string[] = [];
  let failure: 'denied' | 'commit_then_503' | null = null;
  function change(slug: string, id: string, name: string) {
    const owner = records[slug].find((i) => i.canonical_id === id)!;
    former[slug][owner.addressable_name!.toLowerCase()] = id;
    owner.addressable_name = name; owner.name_revision! += 1;
  }
  function classify(slug: string, address: string) {
    const folded = address.toLowerCase();
    const current = records[slug].find((i) => i.canonical_id.toLowerCase() === folded || i.addressable_name?.toLowerCase() === folded);
    const previous = records[slug].find((i) => i.canonical_id === former[slug][folded]);
    return { identity: current ?? previous ?? null, former: !current && !!previous };
  }
  const rename = async (request: Request, slug: string, id: string) => {
    const body = await request.json() as RenameBody;
    puts.push({ slug, id, body });
    if (failure === 'denied') return HttpResponse.json({ detail: { code: 'identity_operator_binding_rejected' } }, { status: 403 });
    const owner = records[slug].find((i) => i.canonical_id === id)!;
    if (body.expected_name_revision !== owner.name_revision) return HttpResponse.json({ detail: { code: 'stale_identity_revision' } }, { status: 409 });
    const collision = classify(slug, body.addressable_name).identity;
    if (collision && collision.canonical_id !== id) return HttpResponse.json({ detail: { code: 'identity_name_unavailable' } }, { status: 409 });
    change(slug, id, body.addressable_name);
    if (failure === 'commit_then_503') return HttpResponse.json({ detail: { code: 'naming_unavailable' } }, { status: 503 });
    return HttpResponse.json(owner);
  };
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'alpha', root: '/mock/alpha' }, { slug: 'beta', root: '/mock/beta' }] })),
    http.get('/api/v1/orgs/:slug/identities', ({ params }) => HttpResponse.json({ identities: records[String(params.slug)] })),
    http.put('/api/v1/orgs/:slug/agents/:id/addressable-name', ({ params, request }) => rename(request, String(params.slug), String(params.id))),
    http.put('/api/v1/orgs/:slug/founder/addressable-name', ({ params, request }) => rename(request, String(params.slug), 'founder')),
    http.post('/api/v1/orgs/:slug/identities/resolve', async ({ params, request }) => {
      const slug = String(params.slug); const body = await request.json() as ResolveBody;
      resolves.push({ slug, body });
      return HttpResponse.json({ resolutions: body.addresses.map((address) => {
        const r = classify(slug, address);
        const eligible = !!r.identity && !r.former && (body.context !== 'task_owner' || r.identity.kind === 'agent');
        return { address, identity: r.identity, status: r.former ? 'former_name' : eligible ? 'resolved' : 'unknown_identity', eligible };
      }) });
    }),
    http.get('/api/v1/orgs/:slug/agents', ({ params }) => HttpResponse.json({ agents: records[String(params.slug)].filter((i) => i.kind === 'agent').map((i) => ({ name: i.canonical_id, team: 'core', role: 'worker', executor: 'claude', model: null, description: '', repos: {}, system_prompt: 'Original prompt', revision: i.canonical_definition_revision, addressable_name: i.addressable_name, name_revision: i.name_revision, naming_status: i.naming_status })) })),
    http.get('/api/v1/orgs/:slug/teams', () => HttpResponse.json({ teams: [] })),
    http.get('/api/v1/orgs/:slug/agents/:id/memory/entries/', () => HttpResponse.json({ entries: [] })),
    http.get('/api/v1/orgs/:slug/agents/:id/cleanup-activity', () => HttpResponse.json({ activities: [] })),
    http.get('/api/v1/orgs/:slug/jobs/', () => HttpResponse.json({ jobs: [] })),
    http.get('/api/v1/health/prereqs', () => HttpResponse.json({ prereqs: [{ tool: 'claude', present: true, path: '/mock/claude', hint: '' }] })),
    http.get('/api/v1/executors/runtime/profiles', () => HttpResponse.json({ profiles: [] })),
    http.get('/api/v1/orgs/:slug/settings', () => HttpResponse.json({ system: {}, org: orgSettings })),
    http.get('/api/v1/orgs/:slug/tasks', () => HttpResponse.json({ tasks: [] })),
    http.get('/api/v1/orgs/:slug/tasks/roots', ({ request }) => { filters.push(new URL(request.url).searchParams.get('assigned_agent') ?? ''); return HttpResponse.json({ tasks: [], next_cursor: null }); }),
    http.get('/api/v1/orgs/:slug/threads', () => HttpResponse.json({ threads: [] })),
    http.get('/api/v1/orgs/:slug/threads/events', () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
    http.get('/api/v1/orgs/:slug/tokens', () => HttpResponse.json({ rollup: [] })),
    http.post('/api/v1/orgs/:slug/artifacts', async ({ params }) => { effects.push({ kind: 'upload', slug: String(params.slug), body: null }); return HttpResponse.json({ name: 'mock-attachment' }); }),
    http.post('/api/v1/orgs/:slug/threads', async ({ params, request }) => { effects.push({ kind: 'compose', slug: String(params.slug), body: await request.json() }); return HttpResponse.json({ thread_id: 'THR-MOCK', started_at: '2026-10-09T00:00:00Z', pending_replies: [] }, { status: 201 }); }),
    http.get('/api/v1/orgs/:slug/threads/THR-MOCK', () => HttpResponse.json({ thread_id: 'THR-MOCK', subject: 'Literal', status: 'open', started_at: '2026-10-09T00:00:00Z', archived_at: null, forwarded_from_id: null, forwarded_from_kind: null, composed_from_dream_id: null, turn_cap: 500, turns_used: 0, summary: null, participants: ['agent_a'], messages: [], reply_delivery: [] })),
    http.get('/api/v1/orgs/:slug/threads/THR-MOCK/messages', () => HttpResponse.json({ messages: [], next_before: null })),
    http.get('/api/v1/orgs/:slug/threads/THR-MOCK/tasks', () => HttpResponse.json({ tasks: [] })),
    http.get('/api/v1/orgs/:slug/threads/THR-MOCK/tail', () => HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } })),
  );
  return { records, puts, resolves, effects, filters, change, fail: (value: typeof failure) => { failure = value; } };
}
function mount(route: string) {
  sessionStorage.setItem('happyranch.token', 'mock-token');
  const client = makeQueryClient();
  render(<MemoryRouter initialEntries={[route]}><I18nProvider mode="full" adapter={savedLocaleAdapter('en')}><AppProvider client={client}>
    <Routes>
      <Route path="/orgs/:slug/agents/:agent_name?" element={<OrgProvider><AgentsPage /></OrgProvider>} />
      <Route path="/orgs/:slug/settings/*" element={<OrgProvider><SettingsPage /></OrgProvider>} />
      <Route path="/orgs/:slug/threads/:thread_id?" element={<OrgProvider><ThreadsPage /></OrgProvider>} />
      <Route path="/orgs/:slug/skills/catalog/:skillId" element={<OrgProvider><SkillDetailPage /></OrgProvider>} />
      <Route path="/orgs/:slug/tasks" element={<OrgProvider><TasksPage /></OrgProvider>} />
    </Routes>
    <LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" />
    <Link to="/orgs/alpha/threads">Mock compose</Link><Link to="/orgs/beta/agents/agent_a">Mock org beta</Link><Link to="/orgs/alpha/agents/agent_a">Mock org alpha</Link>
  </AppProvider></I18nProvider></MemoryRouter>);
  return client;
}
async function agentEditor() {
  const editor = await screen.findByRole('region', { name: t('identity.agentHeading') });
  await waitFor(() => expect(within(editor).getByLabelText(t('identity.name'))).toBeEnabled());
  return editor;
}
async function enterName(editor: HTMLElement, value: string) {
  const user = userEvent.setup(); const input = within(editor).getByLabelText(t('identity.name'));
  await user.clear(input); await user.type(input, value); return input as HTMLInputElement;
}

describe('naming v1 shipping UI — mocked HTTP', () => {
  test.each(['managed', 'user_authored'] as const)('scenario 9 frontend consumer: %s skill rows keep canonical identity and node through label refresh', async (type) => {
    const f = fixture();
    const assignments = [{ agent: 'agent_a', assigned: true, effective: true, state: 'effective' }];
    server.use(
      http.get('/api/v1/orgs/alpha/skills/catalog/naming-proof', () => HttpResponse.json({
        skill_id: 'naming-proof', name: 'Naming fixture', type, source: 'fixture', system_contract: false,
        visibility_category: 'toggleable', policy_class: 'guidance', status: 'active', version: '1',
        validation_state: 'validated', summary: 'Fixture', description: '', when_to_use: '', owner: '', assignments,
      })),
      http.get('/api/v1/orgs/alpha/skills/naming-proof/status', () => HttpResponse.json({ validated: true, assignments })),
    );
    const client = mount('/orgs/alpha/skills/catalog/naming-proof');
    const label = await screen.findByText('Alpha · agent_a');
    const row = label.closest('li')!;
    expect(row).toHaveAttribute('data-agent', 'agent_a');
    const otherRow = type === 'user_authored' ? (await screen.findByText('Other · agent_b')).closest('li') : null;
    if (otherRow) expect(otherRow).toHaveAttribute('data-agent', 'agent_b');
    f.change('alpha', 'agent_a', 'Renamed');
    await act(async () => { await client.invalidateQueries({ queryKey: ['identities', 'alpha'] }); });
    expect((await screen.findByText('Renamed · agent_a')).closest('li')).toBe(row);
    expect(row).toHaveAttribute('data-agent', 'agent_a');
    if (otherRow) expect(screen.getByText('Other · agent_b').closest('li')).toBe(otherRow);
    expect(f.puts).toEqual([]);
  });
  test('scenario 2: actual agent editor saves exact CAS, stale refresh preserves draft and locale/focus until explicit resubmit', async () => {
    const f = fixture(); mount('/orgs/alpha/agents/agent_a');
    const editor = await agentEditor(); const input = await enterName(editor, 'Alex');
    fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    await waitFor(() => expect(f.puts).toHaveLength(1));
    expect(f.puts[0]).toEqual({ slug: 'alpha', id: 'agent_a', body: { addressable_name: 'Alex', expected_name_revision: 1 } });
    await screen.findAllByText('Alex · agent_a');
    await enterName(editor, 'Draft'); f.change('alpha', 'agent_a', 'External');
    fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    await within(editor).findByRole('button', { name: t('identity.resubmit') });
    expect(input.value).toBe('Draft'); expect(f.records.alpha[0].addressable_name).toBe('External');
    input.focus(); input.setSelectionRange(1, 3);
    fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(within(editor).getByLabelText(translate('zh-CN', 'identity.name'))).toBe(input);
    expect(input).toHaveFocus(); expect([input.selectionStart, input.selectionEnd]).toEqual([1, 3]);
    expect(f.puts).toHaveLength(2);
    fireEvent.click(within(editor).getByRole('button', { name: translate('zh-CN', 'identity.resubmit') }));
    await waitFor(() => expect(f.puts).toHaveLength(3));
    expect(f.puts[2].body).toEqual({ addressable_name: 'Draft', expected_name_revision: 3 });
    await waitFor(() => expect(f.records.alpha[0].addressable_name).toBe('Draft'));
  });

  test('scenario 2: ambiguous postcommit error is observed by GET, never retried or labeled confirmed success', async () => {
    const f = fixture(); f.fail('commit_then_503'); mount('/orgs/alpha/agents/agent_a');
    const editor = await agentEditor(); const input = await enterName(editor, 'Observed');
    const save = within(editor).getByRole('button', { name: t('identity.save') }); fireEvent.click(save); fireEvent.click(save);
    await waitFor(() => expect(within(editor).getByRole('status')).toHaveTextContent('request outcome was uncertain'));
    expect(f.puts).toHaveLength(1); expect(input.value).toBe('Observed');
    expect(within(editor).getByRole('status')).toHaveTextContent('Observed · agent_a');
    expect(within(editor).queryByText(/^Saved\./)).not.toBeInTheDocument();
  });

  test('scenario 3: founder rename uses its human route, then a real compose submits @founder and no agent alias', async () => {
    const f = fixture(); mount('/orgs/alpha/settings/organization');
    const editor = await screen.findByRole('region', { name: t('identity.founderHeading') });
    await waitFor(() => expect(within(editor).getByLabelText(t('identity.name'))).toBeEnabled());
    await enterName(editor, 'HumanBoss'); fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    await waitFor(() => expect(f.puts).toHaveLength(1)); expect(f.puts[0].id).toBe('founder');
    expect(f.records.alpha.filter((i) => i.kind === 'agent').map((i) => i.canonical_id)).toEqual(['agent_a', 'agent_b']);
    await waitFor(() => expect(within(editor).getByRole('status')).toHaveTextContent('Saved'));
    fireEvent.click(screen.getByRole('link', { name: 'Mock compose' }));
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.subjectLabel')), 'Human');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.recipientsLabel')), 'HumanBoss');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.bodyLabel')), 'Literal @HumanBoss');
    await user.click(within(dialog).getByRole('button', { name: t('threads.newThread.send') }));
    await waitFor(() => expect(f.effects.filter((e) => e.kind === 'compose')).toHaveLength(1));
    expect(f.effects.find((e) => e.kind === 'compose')?.body).toMatchObject({ recipients: ['@founder'], body_markdown: 'Literal @HumanBoss' });
    expect(f.resolves.some((r) => r.body.context === 'thread_recipient' && r.body.addresses.includes('HumanBoss'))).toBe(true);
  });

  test('scenario 4: case-folded canonical-ID/name collisions and exact invalid spelling leave modeled identity unchanged', async () => {
    const f = fixture(); mount('/orgs/alpha/agents/agent_a'); const editor = await agentEditor();
    const before = JSON.stringify(f.records.alpha); await enterName(editor, 'AGENT_B');
    fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    await waitFor(() => expect(within(editor).getByRole('status')).toHaveTextContent('permanently reserved'));
    expect(JSON.stringify(f.records.alpha)).toBe(before);
    await enterName(editor, ' _label'); fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    expect(f.puts).toHaveLength(1); expect(within(editor).getByLabelText(t('identity.name'))).toHaveValue(' _label');
    expect(JSON.stringify(f.records.alpha)).toBe(before);
  });

  test('scenario 5: former token inside quoted/email body stops associated upload and compose; draft and file remain', async () => {
    const f = fixture(); mount('/orgs/alpha/threads'); const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.subjectLabel')), 'Literal');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.recipientsLabel')), 'agent_a');
    const body = '> quoted: mail@OldAlpha and @Alpha';
    await user.type(within(dialog).getByLabelText(t('threads.newThread.bodyLabel')), body);
    await user.upload(within(dialog).getByLabelText(t('threads.newThread.attachFiles')), new File(['proof'], 'proof.txt', { type: 'text/plain' }));
    await user.click(within(dialog).getByRole('button', { name: t('threads.newThread.send') }));
    await waitFor(() => expect(within(dialog).getByRole('alert')).toHaveTextContent('former name'));
    expect(f.effects).toEqual([]); expect(within(dialog).getByLabelText(t('threads.newThread.bodyLabel'))).toHaveValue(body);
    expect(within(dialog).getByText('proof.txt')).toBeInTheDocument();
    expect(f.resolves.some((r) => r.body.addresses.includes('OldAlpha'))).toBe(true);
  });

  test('scenario 8: real task filter picker keeps selected ID through rename and locale refresh, submits canonical query', async () => {
    const f = fixture(); const client = mount('/orgs/alpha/tasks'); const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: t('tasks.page.filter') }));
    const input = screen.getByLabelText(t('tasks.filters.agent')) as HTMLInputElement;
    // Paste has no keyup to repair a stale deferred change-handler value.
    await user.click(input); await user.paste('Alpha');
    await user.click(await screen.findByRole('option', { name: /Alpha · agent_a/ }));
    expect(input.value).toBe('agent_a'); f.change('alpha', 'agent_a', 'Renamed');
    await act(async () => { await client.invalidateQueries({ queryKey: ['identities', 'alpha'] }); });
    input.focus(); input.setSelectionRange(0, 4); fireEvent.click(screen.getByTestId('test-set-locale-zh-CN'));
    expect(input.value).toBe('agent_a'); expect(input).toHaveFocus(); expect(input.selectionEnd).toBe(4);
    fireEvent.click(screen.getByRole('button', { name: translate('zh-CN', 'tasks.filters.apply') }));
    await waitFor(() => expect(f.filters).toContain('agent_a')); expect(f.filters).not.toContain('Renamed');
  });

  test.each(['success', 'failure'] as const)('scenario 8: real task filter picker Clear invalidates deferred resolution %s', async (outcome) => {
    fixture();
    const queries: URLSearchParams[] = [];
    let finish!: () => void;
    const deferred = new Promise<void>((resolve) => { finish = resolve; });
    let arrived = false;
    server.use(
      http.get('/api/v1/orgs/alpha/tasks/roots', ({ request }) => {
        queries.push(new URL(request.url).searchParams);
        return HttpResponse.json({ tasks: [], next_cursor: null });
      }),
      http.post('/api/v1/orgs/alpha/identities/resolve', async ({ request }) => {
        const body = await request.json() as ResolveBody;
        expect(body.addresses).toEqual(['Alpha']);
        arrived = true;
        await deferred;
        return outcome === 'success'
          ? HttpResponse.json({ resolutions: [{ address: 'Alpha', identity: { canonical_id: 'agent_a', kind: 'agent', lifecycle: 'active' }, status: 'resolved', eligible: true }] })
          : HttpResponse.json({ detail: { code: 'naming_unavailable' } }, { status: 503 });
      }),
    );
    const client = mount('/orgs/alpha/tasks');
    const user = userEvent.setup();
    try {
      await waitFor(() => expect(queries.length).toBeGreaterThan(0));
      await user.click(await screen.findByRole('button', { name: t('tasks.page.filter') }));
      const agent = screen.getByLabelText(t('tasks.filters.agent'));
      const status = screen.getByLabelText(t('tasks.filters.statusSelect'));
      await user.type(agent, 'Alpha');
      await user.selectOptions(status, 'completed');
      await user.click(screen.getByRole('button', { name: t('tasks.filters.apply') }));
      await waitFor(() => expect(arrived).toBe(true));
      const initialQueries = queries.map((query) => query.toString());
      await user.click(screen.getByRole('button', { name: t('tasks.filters.clear') }));
      expect(agent).toHaveValue('');
      expect(status).toHaveValue('');
      await act(async () => { finish(); });
      await waitFor(() => expect(client.isMutating()).toBe(0));
      expect(queries.map((query) => query.toString())).toEqual(initialQueries);
      expect(queries.some((query) => query.has('assigned_agent') || query.get('status') === 'completed')).toBe(false);
      expect(screen.getByLabelText(t('tasks.filters.agent'))).toHaveValue('');
      expect(screen.getByLabelText(t('tasks.filters.statusSelect'))).toHaveValue('');
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.queryByText((text) => text.startsWith(t('tasks.filters.applied')))).not.toBeInTheDocument();
    } finally {
      await act(async () => { finish(); });
      client.clear();
    }
  });

  test('scenario 8: a selected thread recipient remains its original ID after external rename', async () => {
    const f = fixture(); const client = mount('/orgs/alpha/threads'); const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /New thread/i }));
    const dialog = screen.getByRole('dialog');
    await user.type(within(dialog).getByLabelText(t('threads.newThread.subjectLabel')), 'Selected');
    const recipient = within(dialog).getByLabelText(t('threads.newThread.recipientsLabel'));
    await user.type(recipient, 'Alpha');
    await user.click(await screen.findByRole('option', { name: /Alpha · agent_a/ }));
    expect(recipient).toHaveValue('agent_a, ');
    f.change('alpha', 'agent_a', 'Renamed');
    await act(async () => { await client.invalidateQueries({ queryKey: ['identities', 'alpha'] }); });
    expect(recipient).toHaveValue('agent_a, ');
    const literal = 'Keep literal @unrecognized';
    await user.type(within(dialog).getByLabelText(t('threads.newThread.bodyLabel')), literal);
    await user.click(within(dialog).getByRole('button', { name: t('threads.newThread.send') }));
    await waitFor(() => expect(f.effects.filter((e) => e.kind === 'compose')).toHaveLength(1));
    expect(f.effects.find((e) => e.kind === 'compose')?.body).toMatchObject({ recipients: ['agent_a'], body_markdown: literal });
  });

  test('scenario 10: denial preserves draft and identity; org-scoped current labels and drafts never become the other org’s identity', async () => {
    const f = fixture(); f.fail('denied'); mount('/orgs/alpha/agents/agent_a');
    const editor = await agentEditor(); const input = await enterName(editor, 'KeepDraft');
    const before = JSON.stringify(f.records); fireEvent.click(within(editor).getByRole('button', { name: t('identity.save') }));
    await waitFor(() => expect(within(editor).getByRole('status')).toHaveTextContent('denied'));
    expect(JSON.stringify(f.records)).toBe(before); expect(input.value).toBe('KeepDraft');
    fireEvent.click(screen.getByRole('link', { name: 'Mock org beta' }));
    await screen.findAllByText('BetaAlpha · agent_a');
    await waitFor(() => expect(within(editor).getByLabelText(t('identity.name'))).toHaveValue('BetaAlpha'));
    fireEvent.click(screen.getByRole('link', { name: 'Mock org alpha' }));
    await waitFor(() => expect(within(editor).getByLabelText(t('identity.name'))).toHaveValue('KeepDraft'));
    expect(f.puts).toHaveLength(1); expect(f.puts[0].slug).toBe('alpha');
  });
});
