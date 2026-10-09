/** THR280 C01/C03/C04/C06/C09/C10/C17: real pane, providers and HTTP seam. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, expect, test } from 'vitest';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { AgentDetailDrawer } from './AgentDetailDrawer';
import type { AgentSummary } from '@/lib/api/types';

const root = '/api/v1/orgs/prompt-test';
const base = { name: 'writer', team: 'engineering', role: 'worker', executor: 'claude',
  model: null, repos: {}, description: 'unchanged', system_prompt: 'OLD\n', revision: 'a'.repeat(64) };
const authored = '# 指令 🐎\n\n正文  \n\n    code\n\n```text\n例子\n```\n';

beforeEach(() => { sessionStorage.setItem('happyranch.token', 'tok'); });

function FixtureControls({ observe }: { observe: (client: QueryClient) => void }): JSX.Element {
  const client = useQueryClient();
  observe(client);
  const navigate = useNavigate();
  return <><button onClick={() => void client.invalidateQueries({ queryKey: ['agents', 'prompt-test'] })}>poll fixture</button>
    <button onClick={() => navigate('/orgs/prompt-test/agents/other')}>select other fixture</button>
    <button onClick={() => navigate('/orgs/prompt-test/agents/writer')}>select writer fixture</button>
    <button onClick={() => navigate('/orgs/prompt-beta/agents/writer')}>select beta fixture</button></>;
}

function setup(read: Parameters<typeof http.get>[1], put: Parameters<typeof http.put>[1], surface = 'pane') {
  let client!: QueryClient;
  server.use(
    http.get('/api/v1/auth/bootstrap', () => HttpResponse.json({ token: 'tok' })),
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: 'prompt-test', root: '/fixture' }] })),
    http.get(`${root}/agents`, read),
    http.put(`${root}/agents/writer/system-prompt`, put),
    http.get('/api/v1/health/prereqs', () => HttpResponse.json({ prereqs: [] })),
    http.get('/api/v1/executors/runtime/profiles', () => HttpResponse.json({ profiles: [] })),
    http.get(`${root}/teams`, () => HttpResponse.json({ teams: [] })),
    http.get(`${root}/tasks`, () => HttpResponse.json({ tasks: [] })),
    http.get(`${root}/jobs/`, () => HttpResponse.json({ jobs: [] })),
    http.get(`${root}/agents/:name/cleanup-activity`, () => HttpResponse.json({ activities: [] })),
    http.get(`${root}/agents/:name/memory/entries/`, () => HttpResponse.json({ entries: [] })),
    http.all('/api/v1/*', () => HttpResponse.json({})),
  );
  const rendered = renderWithProviders(<>{surface === 'pane' ? <AppRoutes /> :
    <Routes><Route path="/orgs/:slug/agents/:agentName" element={<AgentDetailDrawer agentName="writer" />} /></Routes>}
    <FixtureControls observe={(value) => { client = value; }} /><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>, {
    route: '/orgs/prompt-test/agents/writer', i18n: { adapter: savedLocaleAdapter('en') },
  });
  return { ...rendered, client };
}

test.each(['pane', 'drawer'])('%s preserves authored text and waits for matching fresh body AND revision before Saved', async (surface) => {
  let current = { ...base };
  const writes: unknown[] = [];
  let releaseGet!: () => void;
  const readback = new Promise<void>((resolve) => { releaseGet = resolve; });
  setup(async () => {
    if (writes.length) await readback;
    return HttpResponse.json({ agents: [current] });
  }, async ({ request }) => {
    const body = await request.json(); writes.push(body);
    current = { ...base, system_prompt: authored, revision: 'b'.repeat(64) };
    return HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: current.revision });
  }, surface);
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled());
  fireEvent.click(edit);
  const textarea = screen.getByRole('textbox', { name: 'System prompt' });
  fireEvent.change(textarea, { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  await waitFor(() => expect(writes).toEqual([{ system_prompt: authored, expected_revision: base.revision }]));
  expect(textarea).toBeDisabled();
  expect(within(screen.getByRole('textbox', { name: 'System prompt' }).closest('section')!).getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  releaseGet();
  expect(await screen.findByText('Saved')).toBeInTheDocument();
  expect(screen.getByText('# 指令 🐎', { exact: false }).textContent).toBe(authored);
});

test('cancel and locale switch retain the same draft node, focus and selection without PUT', async () => {
  const writes: unknown[] = [];
  setup(() => HttpResponse.json({ agents: [base] }), async ({ request }) => {
    writes.push(await request.json()); return HttpResponse.json({});
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled());
  fireEvent.click(edit);
  const textarea = screen.getByRole('textbox', { name: 'System prompt' }) as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: authored } });
  textarea.focus(); textarea.setSelectionRange(3, 8);
  fireEvent.click(screen.getByRole('button', { name: 'switch to zh-CN' }));
  expect(screen.getByRole('textbox', { name: '系统提示词' })).toBe(textarea);
  expect(textarea.value).toBe(authored);
  expect(textarea.selectionStart).toBe(3); expect(textarea.selectionEnd).toBe(8);
  expect(textarea).toHaveFocus();
  fireEvent.click(within(screen.getByRole('textbox', { name: '系统提示词' }).closest('section')!).getByRole('button', { name: '取消' }));
  expect(writes).toEqual([]);
  expect(screen.queryByRole('textbox', { name: '系统提示词' })).not.toBeInTheDocument();
});

test.each(['body', 'revision', 'missing', 'read-error', 'invalid-body', 'invalid-revision'] as const)('readback %s mismatch retains recoverable draft', async (mismatch) => {
  let wrote = false; let puts = 0;
  const { client } = setup(() => {
    if (wrote && mismatch === 'read-error') return HttpResponse.json({ detail: 'unavailable' }, { status: 500 });
    const current = !wrote ? base : { ...base,
      system_prompt: mismatch === 'invalid-body' ? null : mismatch === 'body' ? 'OTHER\n' : authored,
      revision: mismatch === 'invalid-revision' ? 'B'.repeat(64) : mismatch === 'revision' ? base.revision : 'b'.repeat(64) };
    return HttpResponse.json({ agents: wrote && mismatch === 'missing' ? [] : [current] });
  }, () => {
    wrote = true; puts += 1;
    return HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: 'b'.repeat(64) });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled());
  fireEvent.click(edit);
  const textarea = screen.getByRole('textbox', { name: 'System prompt' });
  fireEvent.change(textarea, { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is kept');
  expect(textarea).toHaveValue(authored);
  expect(textarea).not.toBeDisabled();
  expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  expect(puts).toBe(1);
  expect(screen.getByRole('button', { name: 'Save system prompt' })).toBeDisabled();
  if (mismatch.startsWith('invalid')) expect(client.getQueryData<{ agents: AgentSummary[] }>(['agents', 'prompt-test']))
    .toEqual({ agents: [base] });
});

test.each(['ctrlKey', 'metaKey'] as const)('%s plus repeated click sends one frozen PUT', async (modifier) => {
  let puts = 0; let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  setup(() => HttpResponse.json({ agents: [base] }), async () => {
    puts += 1; await pending;
    return HttpResponse.json({ detail: { code: 'stale_agent_revision', current_revision: 'c'.repeat(64) } }, { status: 409 });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  const textarea = screen.getByRole('textbox', { name: 'System prompt' });
  fireEvent.change(textarea, { target: { value: authored } });
  fireEvent.keyDown(textarea, { key: 's', [modifier]: true });
  fireEvent.keyDown(textarea, { key: 's', [modifier]: true });
  fireEvent.click(screen.getByRole('button', { name: 'Saving and verifying…' }));
  await waitFor(() => expect(puts).toBe(1));
  expect(textarea).toBeDisabled();
  release();
  expect(await screen.findByRole('alert')).toHaveTextContent('The agent changed');
  expect(puts).toBe(1);
});

test.each([undefined, null, '', 'a', 'A'.repeat(64)])('missing or malformed revision %s refuses edit', async (revision) => {
  let puts = 0;
  setup(() => HttpResponse.json({ agents: [{ ...base, revision }] }), () => { puts += 1; return HttpResponse.json({}); });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await screen.findByText('A valid revision is unavailable. Reload before editing. Your draft is kept.');
  expect(edit).toBeDisabled();
  expect(puts).toBe(0);
});

test('dirty polling cannot grant a new revision to the frozen draft', async () => {
  let reads = 0; const writes: unknown[] = [];
  setup(() => {
    reads += 1;
    return HttpResponse.json({ agents: [{ ...base, revision: reads === 1 ? base.revision : 'c'.repeat(64) }] });
  }, async ({ request }) => {
    writes.push(await request.json());
    return HttpResponse.json({ detail: { code: 'stale_agent_revision', current_revision: 'c'.repeat(64) } }, { status: 409 });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'poll fixture' }));
  await waitFor(() => expect(reads).toBe(2));
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('The agent changed');
  expect(writes).toHaveLength(1);
  expect((writes[0] as { expected_revision: string }).expected_revision).toBe(base.revision);
  expect(writes).toEqual([{ system_prompt: authored, expected_revision: base.revision }]);
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue(authored);
});

test.each(['put', 'get'])('late %s after selecting another agent never changes the new editor', async (phase) => {
  let release!: () => void; let waiting = false; let writes = 0;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  setup(async () => {
    if (writes && phase === 'get') { waiting = true; await pending; }
    return HttpResponse.json({ agents: [base, { ...base, name: 'other', system_prompt: 'OTHER\n' }] });
  }, async () => {
    writes += 1;
    if (phase === 'put') { waiting = true; await pending; }
    return HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: 'b'.repeat(64) });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  await waitFor(() => expect(waiting).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'select other fixture' }));
  const otherEdit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(otherEdit).toBeEnabled()); fireEvent.click(otherEdit);
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('OTHER\n');
  release();
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'System prompt' })).toBeEnabled());
  expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(writes).toBe(1);
});

test('initial failed GET can recover with explicit GET without a PUT', async () => {
  let reads = 0; let puts = 0;
  setup(() => {
    reads += 1;
    return reads === 1 ? HttpResponse.json({ detail: 'offline' }, { status: 500 }) : HttpResponse.json({ agents: [base] });
  }, () => { puts += 1; return HttpResponse.json({}); });
  expect(await screen.findByText('Could not load the system prompt. Reload before editing.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload latest' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Edit system prompt' })).toBeEnabled());
  expect(puts).toBe(0);
});

test.each(['validation', 'reconciliation', 'audit', 'network-before', 'network-after', 'conflict'] as const)(
  '%s failure keeps the same localized draft and requires GET inspection before reapply', async (fault) => {
    let reads = 0; let puts = 0; let current = { ...base };
    setup(() => { reads += 1; return HttpResponse.json({ agents: [current] }); }, () => {
      puts += 1;
      if (fault === 'audit' || fault === 'network-after') {
        current = { ...base, system_prompt: authored, revision: 'b'.repeat(64) };
      }
      if (fault.startsWith('network')) return HttpResponse.error();
      const detail = fault === 'audit' ? { code: 'system_prompt_audit_failed', commit_state: 'possibly_committed' }
        : fault === 'reconciliation' ? { code: 'system_prompt_reconciliation_failed', error: 'raw fixture diagnostic 中文',
          compensation: { canonical: 'failed', workspace: 'not_owned' } }
        : fault === 'conflict' ? { code: 'stale_agent_revision', current_revision: 'c'.repeat(64) }
        : { code: 'expected_revision_required' };
      return HttpResponse.json({ detail }, { status: fault === 'audit' ? 500 : fault === 'reconciliation' ? 400 : fault === 'conflict' ? 409 : 422 });
    });
    const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
    await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
    const textarea = screen.getByRole('textbox', { name: 'System prompt' }) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: authored } });
    fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is kept');
    expect(textarea).toHaveValue(authored);
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save system prompt' })).toBeDisabled();
    const observedReads = reads;
    textarea.focus(); textarea.setSelectionRange(3, 8);
    fireEvent.click(screen.getByRole('button', { name: 'switch to zh-CN' }));
    expect(screen.getByRole('textbox', { name: '系统提示词' })).toBe(textarea);
    expect(textarea).toHaveValue(authored); expect(textarea).toHaveFocus();
    expect(textarea.selectionStart).toBe(3); expect(textarea.selectionEnd).toBe(8);
    expect(screen.getByRole('alert')).toHaveTextContent('草稿已保留');
    if (fault === 'reconciliation') expect(screen.getByRole('alert')).toHaveTextContent('raw fixture diagnostic 中文');
    fireEvent.click(screen.getByRole('button', { name: 'switch to en' }));
    expect(reads).toBe(observedReads); expect(puts).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Reload latest' }));
    await waitFor(() => expect(reads).toBe(observedReads + 1));
    await waitFor(() => expect(textarea).toBeEnabled());
    expect(textarea).toHaveValue(authored); expect(puts).toBe(1);
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save system prompt' })).toHaveProperty('disabled',
      fault === 'audit' || fault === 'network-after');
  },
);

test.each(['put-success', 'put-error', 'get-success', 'get-error'] as const)(
  'late %s after same-name org navigation cannot alter beta draft', async (phase) => {
    let release!: () => void; let waiting = false; let puts = 0; let settled = false;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    setup(async () => {
      if (puts && phase.startsWith('get')) { waiting = true; await pending; settled = true; }
      return phase === 'get-error' && puts ? HttpResponse.json({ detail: 'offline' }, { status: 500 })
        : HttpResponse.json({ agents: [{ ...base, system_prompt: puts ? authored : base.system_prompt,
          revision: puts ? 'b'.repeat(64) : base.revision }] });
    }, async () => {
      puts += 1;
      if (phase.startsWith('put')) { waiting = true; await pending; settled = true; }
      return phase === 'put-error' ? HttpResponse.json({ detail: 'offline' }, { status: 500 })
        : HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: 'b'.repeat(64) });
    });
    server.use(
      http.get('/api/v1/orgs/prompt-beta/agents', () => HttpResponse.json({
        agents: [{ ...base, system_prompt: 'BETA PROMPT\n', revision: 'c'.repeat(64) }],
      })),
      http.get('/api/v1/orgs/prompt-beta/agents/:name/memory/entries/', () => HttpResponse.json({ entries: [] })),
      http.get('/api/v1/orgs/prompt-beta/agents/:name/cleanup-activity', () => HttpResponse.json({ activities: [] })),
      http.get('/api/v1/orgs/prompt-beta/teams', () => HttpResponse.json({ teams: [] })),
      http.get('/api/v1/orgs/prompt-beta/tasks', () => HttpResponse.json({ tasks: [] })),
      http.get('/api/v1/orgs/prompt-beta/jobs/', () => HttpResponse.json({ jobs: [] })),
    );
    const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
    await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
    fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored } });
    fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
    await waitFor(() => expect(waiting).toBe(true));
    fireEvent.click(screen.getByRole('button', { name: 'select beta fixture' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit system prompt' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Edit system prompt' }));
    const betaText = screen.getByRole('textbox', { name: 'System prompt' });
    expect(betaText).toHaveValue('BETA PROMPT\n');
    release(); await waitFor(() => expect(settled).toBe(true));
    expect(betaText).toHaveValue('BETA PROMPT\n'); expect(betaText).toBeEnabled();
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument(); expect(puts).toBe(1);
  },
);

test('verified prompt survives navigation away and return', async () => {
  let current = { ...base }; let reads = 0; const writes: unknown[] = [];
  const requests: Array<{ fresh: boolean; cache: string | null }> = [];
  const { client } = setup(({ request }) => {
    requests.push({ fresh: new URL(request.url).searchParams.has('_prompt_readback'), cache: request.headers.get('Cache-Control') });
    reads += 1; return HttpResponse.json({ agents: [
    reads === 1 ? current : { ...current, description: 'stale unrelated field' },
    { ...base, name: 'other', system_prompt: reads === 1 ? 'OTHER\n' : 'stale other' },
  ] }); }, async ({ request }) => {
    writes.push(await request.json());
    current = { ...base, system_prompt: authored, revision: 'b'.repeat(64) };
    return HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: current.revision });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored.slice(0, -1) } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  expect(await screen.findByText('Saved')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'select other fixture' }));
  await screen.findByRole('heading', { name: 'other', level: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'Edit system prompt' }));
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('OTHER\n');
  fireEvent.click(screen.getByRole('button', { name: 'select writer fixture' }));
  const returnedEdit = await screen.findByRole('button', { name: 'Edit system prompt' });
  fireEvent.click(returnedEdit);
  expect(reads).toBe(2);
  expect(requests).toEqual([{ fresh: false, cache: null }, { fresh: true, cache: 'no-cache, no-store' }]);
  expect(client.getQueryData<{ agents: AgentSummary[] }>(['agents', 'prompt-test'])?.agents)
    .toEqual([{ ...base, system_prompt: authored, revision: 'b'.repeat(64) },
      { ...base, name: 'other', system_prompt: 'OTHER\n' }]);
  expect(writes).toEqual([{ system_prompt: authored.slice(0, -1), expected_revision: base.revision }]);
  expect(screen.queryByText('stale unrelated field')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue(authored);
});

test('returned editor uses verified revision for the next save', async () => {
  let current = { ...base }; const writes: Array<{system_prompt: string; expected_revision: string}> = [];
  const observations: Array<{ system_prompt: string; revision: string }> = [];
  const { client } = setup(({ request }) => {
    if (new URL(request.url).searchParams.has('_prompt_readback')) observations.push({ system_prompt: current.system_prompt, revision: current.revision });
    return HttpResponse.json({ agents: [current, { ...base, name: 'other' }] });
  }, async ({request}) => {
    const body = await request.json() as {system_prompt: string; expected_revision: string}; writes.push(body);
    if (body.expected_revision !== current.revision) return HttpResponse.json({ detail: {code: 'stale_agent_revision', current_revision: current.revision} }, {status: 409});
    current = { ...base, system_prompt: writes.length === 1 ? authored : 'FOLLOWUP\n', revision: writes.length === 1 ? 'b'.repeat(64) : 'c'.repeat(64) };
    return HttpResponse.json({ agent: 'writer', system_prompt: current.system_prompt, revision: current.revision });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored.slice(0, -1) } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  expect(await screen.findByText('Saved')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'select other fixture' }));
  await screen.findByRole('heading', { name: 'other', level: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'select writer fixture' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit system prompt' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: 'FOLLOWUP\n' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes).toEqual([{ system_prompt: authored.slice(0, -1), expected_revision: 'a'.repeat(64) },
    { system_prompt: 'FOLLOWUP\n', expected_revision: 'b'.repeat(64) }]);
  expect(await screen.findByText('Saved')).toBeInTheDocument();
  expect(screen.getByText('FOLLOWUP').textContent).toBe('FOLLOWUP\n');
  expect(observations).toEqual([{ system_prompt: authored, revision: 'b'.repeat(64) },
    { system_prompt: 'FOLLOWUP\n', revision: 'c'.repeat(64) }]);
  expect(client.getQueryData<{ agents: AgentSummary[] }>(['agents', 'prompt-test'])?.agents[0])
    .toMatchObject({ system_prompt: 'FOLLOWUP\n', revision: 'c'.repeat(64) });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Edit system prompt' }));
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue('FOLLOWUP\n');
});

test.each(['winner', 'aba'] as const)('a newer observed prompt %s wins over a held readback without blessing the dirty draft', async (sequence) => {
  let current = { ...base }; let reads = 0; let waiting = false; let settled = false;
  let release!: () => void; const pending = new Promise<void>((resolve) => { release = resolve; });
  const writes: Array<{ system_prompt: string; expected_revision: string }> = [];
  const { client } = setup(async () => {
    reads += 1; const observed = { ...current };
    if (reads === 2) { waiting = true; await pending; settled = true; }
    return HttpResponse.json({ agents: [observed, { ...base, name: 'other' }] });
  }, async ({ request }) => {
    const body = await request.json() as { system_prompt: string; expected_revision: string }; writes.push(body);
    if (body.expected_revision !== current.revision) return HttpResponse.json({
      detail: { code: 'stale_agent_revision', current_revision: current.revision },
    }, { status: 409 });
    current = { ...base, system_prompt: body.system_prompt, revision: writes.length === 1 ? 'b'.repeat(64) : 'd'.repeat(64) };
    return HttpResponse.json({ agent: 'writer', system_prompt: current.system_prompt, revision: current.revision });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  const textarea = screen.getByRole('textbox', { name: 'System prompt' });
  fireEvent.change(textarea, { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  await waitFor(() => expect(waiting).toBe(true));
  const winner = sequence === 'aba' ? { ...base } : { ...base, system_prompt: 'WINNER\n', revision: 'c'.repeat(64) };
  current = winner;
  const observation = client.getQueryState(['agents', 'prompt-test'])?.dataUpdateCount;
  fireEvent.click(screen.getByRole('button', { name: 'poll fixture' }));
  await waitFor(() => expect(client.getQueryState(['agents', 'prompt-test'])?.dataUpdateCount).toBe((observation ?? 0) + 1));
  release(); await waitFor(() => expect(settled).toBe(true));
  expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is kept');
  expect(textarea).toHaveValue(authored);
  expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save system prompt' })).toBeDisabled();
  fireEvent.click(within(screen.getByRole('textbox', { name: 'System prompt' }).closest('section')!).getByRole('button', { name: 'Cancel' }));
  fireEvent.click(screen.getByRole('button', { name: 'select other fixture' }));
  await screen.findByRole('heading', { name: 'other', level: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'select writer fixture' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit system prompt' }));
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue(winner.system_prompt);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: 'FOLLOWUP\n' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  expect(await screen.findByText('Saved')).toBeInTheDocument();
  expect(writes).toEqual([{ system_prompt: authored, expected_revision: base.revision },
    { system_prompt: 'FOLLOWUP\n', expected_revision: winner.revision }]);
  expect(screen.getByText('FOLLOWUP').textContent).toBe('FOLLOWUP\n');
});

test('an older ordinary read cannot overwrite the verified return snapshot', async () => {
  let reads = 0; let current = { ...base }; let readbackWaiting = false; let pollWaiting = false; let pollSettled = false;
  let releaseRead!: () => void; let releasePoll!: () => void;
  const readback = new Promise<void>((resolve) => { releaseRead = resolve; });
  const poll = new Promise<void>((resolve) => { releasePoll = resolve; });
  const { client } = setup(async () => {
    const readNumber = ++reads;
    if (readNumber === 2) { readbackWaiting = true; await readback; }
    if (readNumber === 3) { pollWaiting = true; await poll; pollSettled = true; return HttpResponse.json({ agents: [base, { ...base, name: 'other' }] }); }
    return HttpResponse.json({ agents: [current, { ...base, name: 'other' }] });
  }, () => {
    current = { ...base, system_prompt: authored, revision: 'b'.repeat(64) };
    return HttpResponse.json({ agent: 'writer', system_prompt: authored, revision: current.revision });
  });
  const edit = await screen.findByRole('button', { name: 'Edit system prompt' });
  await waitFor(() => expect(edit).toBeEnabled()); fireEvent.click(edit);
  fireEvent.change(screen.getByRole('textbox', { name: 'System prompt' }), { target: { value: authored } });
  fireEvent.click(screen.getByRole('button', { name: 'Save system prompt' }));
  await waitFor(() => expect(readbackWaiting).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'poll fixture' }));
  await waitFor(() => expect(pollWaiting).toBe(true));
  releaseRead(); expect(await screen.findByText('Saved')).toBeInTheDocument();
  const pollDelivered = new Promise<void>((resolve) => {
    server.events.on('response:mocked', function acknowledgePoll({ request }) {
      const url = new URL(request.url);
      if (url.pathname === `${root}/agents` && !url.searchParams.has('_prompt_readback') && pollSettled) {
        server.events.removeListener('response:mocked', acknowledgePoll);
        resolve();
      }
    });
  });
  await act(async () => { releasePoll(); await pollDelivered; });
  await waitFor(() => expect(pollSettled).toBe(true));
  expect(client.getQueryData<{ agents: AgentSummary[] }>(['agents', 'prompt-test'])?.agents[0])
    .toMatchObject({ system_prompt: authored, revision: 'b'.repeat(64) });
  fireEvent.click(screen.getByRole('button', { name: 'select other fixture' }));
  await screen.findByRole('heading', { name: 'other', level: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'select writer fixture' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit system prompt' }));
  expect(screen.getByRole('textbox', { name: 'System prompt' })).toHaveValue(authored);
  expect(reads).toBe(3);
});
