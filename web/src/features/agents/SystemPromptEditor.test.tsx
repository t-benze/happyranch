/** THR280 C01/C03/C04/C06/C09/C10/C17: real pane, providers and HTTP seam. */
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeEach, expect, test } from 'vitest';
import { useQueryClient } from '@tanstack/react-query';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { AppRoutes } from '@/routes';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { AgentDetailDrawer } from './AgentDetailDrawer';

const root = '/api/v1/orgs/prompt-test';
const base = { name: 'writer', team: 'engineering', role: 'worker', executor: 'claude',
  model: null, repos: {}, description: 'unchanged', system_prompt: 'OLD\n', revision: 'a'.repeat(64) };
const authored = '# 指令 🐎\n\n正文  \n\n    code\n\n```text\n例子\n```\n';

beforeEach(() => { sessionStorage.setItem('happyranch.token', 'tok'); });

function FixtureControls(): JSX.Element {
  const client = useQueryClient();
  const navigate = useNavigate();
  return <><button onClick={() => void client.invalidateQueries({ queryKey: ['agents', 'prompt-test'] })}>poll fixture</button>
    <button onClick={() => navigate('/orgs/prompt-test/agents/other')}>select other fixture</button></>;
}

function setup(read: () => Response | Promise<Response>, put: Parameters<typeof http.put>[1], surface = 'pane') {
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
  return renderWithProviders(<>{surface === 'pane' ? <AppRoutes /> :
    <Routes><Route path="/orgs/:slug/agents/:agentName" element={<AgentDetailDrawer agentName="writer" />} /></Routes>}
    <FixtureControls /><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>, {
    route: '/orgs/prompt-test/agents/writer', i18n: { adapter: savedLocaleAdapter('en') },
  });
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
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
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
  fireEvent.click(screen.getByRole('button', { name: '取消' }));
  expect(writes).toEqual([]);
  expect(screen.queryByRole('textbox', { name: '系统提示词' })).not.toBeInTheDocument();
});

test.each(['body', 'revision', 'missing', 'read-error'] as const)('readback %s mismatch retains recoverable draft', async (mismatch) => {
  let wrote = false; let puts = 0;
  setup(() => {
    if (wrote && mismatch === 'read-error') return HttpResponse.json({ detail: 'unavailable' }, { status: 500 });
    const current = !wrote ? base : { ...base, system_prompt: mismatch === 'body' ? 'OTHER\n' : authored,
      revision: mismatch === 'revision' ? base.revision : 'b'.repeat(64) };
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
