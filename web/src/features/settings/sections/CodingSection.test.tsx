/**
 * Founder coding settings page — rendered UI/transport boundary (THR-320
 * F6/F8-R2f). Real route, provider and API client over MSW. These checks
 * establish UI/client behavior only; real Founder persistence, authority and
 * native execution stay root-owned acceptance.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { Route, Routes } from 'react-router-dom';
import { SettingsPage } from '../SettingsPage';
import { renderGuarded } from './capacityTestMount';
import { server } from '@/test/server';
import { LocaleTestSwitch } from '@/test/render';

const A = 'org-a';
const B = 'org-b';
const DIGEST = 'a'.repeat(64);
const RELEASE = {
  identity: 'release/happyranch/coding',
  version: 1,
  definition_digest: '6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8',
} as const;

const ROSTER = [
  { name: 'dev_agent', team: 'engineering', role: 'worker', executor: 'claude', description: '', repos: { happyranch: 'x' }, system_prompt: '' },
  { name: 'code_reviewer', team: 'engineering', role: 'worker', executor: 'codex', description: '', repos: { happyranch: 'x' }, system_prompt: '' },
  { name: 'qa_engineer', team: 'quality', role: 'worker', executor: 'claude', description: '', repos: { happyranch: 'x' }, system_prompt: '' },
  { name: 'frontend_engineer', team: 'design', role: 'worker', executor: 'claude', description: '', repos: { happyranch: 'x' }, system_prompt: '' },
  { name: 'engineering_manager', team: 'engineering', role: 'manager', executor: 'claude', description: '', repos: { happyranch: 'x' }, system_prompt: '' },
];
const TEAMS = [
  { name: 'engineering', manager: 'engineering_manager', manager_kind: 'agent', workers: ['dev_agent', 'code_reviewer'] },
  { name: 'quality', manager: 'founder', manager_kind: 'human', workers: ['qa_engineer'] },
  { name: 'design', manager: 'founder', manager_kind: 'human', workers: ['frontend_engineer'] },
];

function configuration(implementer = 'dev_agent') {
  return {
    release: RELEASE,
    roles: {
      implementer: { principal: implementer, team: implementer === 'retired_dev' ? 'legacy' : 'engineering' },
      code_reviewer: { principal: 'code_reviewer', team: 'engineering' },
      qa_engineer: { principal: 'qa_engineer', team: 'quality' },
      agent_manager: { principal: 'engineering_manager', team: 'engineering' },
    },
    eligible_replacements: {
      implementer: [{ principal: 'frontend_engineer', team: 'design' }],
      code_reviewer: [], qa_engineer: [], agent_manager: [],
    },
    repository_references: [{ repository_id: 'repo-1', repo_key: 'happyranch', origin_digest: DIGEST, object_format: 'sha1' }],
    authority: { namespace: 'org/org-a', generation: 7, snapshot_digest: DIGEST },
  };
}

function current(generation: number, mapping: number, admission: 'disabled' | 'explicit' = 'explicit') {
  return generation === 0
    ? { generation: 0, admission: 'disabled', release: null, mapping: null, snapshot_digest: null, mapping_generation: mapping }
    : { generation, admission, release: RELEASE, mapping: { id: `coding-mapping:${mapping}`, generation: mapping, digest: DIGEST }, snapshot_digest: DIGEST, mapping_generation: mapping };
}

function view(slug: string, gen: number, mapping: number, config: unknown, admission: 'disabled' | 'explicit' = 'explicit') {
  return { format: 'workflow-coding-settings-view@1', org_slug: slug, current: current(gen, mapping, admission), configuration: config, operation: null };
}

function receipt(key: string, generation: number) {
  return {
    format: 'workflow-coding-settings-receipt@1', operation_key: key, request_digest: DIGEST,
    snapshot: {
      format: 'workflow-coding-settings-snapshot@1', org_slug: A, generation, admission: 'explicit', release: RELEASE,
      mapping: { id: 'coding-mapping:x', generation: generation + 3, digest: DIGEST }, operation_key: key,
      request_digest: DIGEST, previous_snapshot_digest: null, published_at: '2026-10-22T00:00:00.000000Z', published_by: 'human:founder',
    },
    snapshot_digest: DIGEST, publication_audit_id: 9, replayed: generation === 1 ? false : true,
  };
}

function options(slug = A, generation = 7) {
  return { format: 'workflow-coding-settings-options@1', org_slug: slug, release: RELEASE,
    authority: { namespace: `org/${slug}`, generation, snapshot_digest: DIGEST },
    repository_choices: [{ repository_id: 'workflow-repository:' + 'b'.repeat(64), repo_key: 'happyranch', origin_digest: DIGEST }],
    object_formats: ['sha1', 'sha256'] };
}

interface Seen { method: string; path: string; search: string; body: unknown }
let seen: Seen[] = [];

function stubShell(slug: string) {
  server.use(
    http.get(`/api/v1/orgs/${slug}/settings`, () => HttpResponse.json({ system: {}, org: {} })),
    http.get(`/api/v1/orgs/${slug}/agents`, () => HttpResponse.json({ agents: ROSTER })),
    http.get(`/api/v1/orgs/${slug}/teams`, () => HttpResponse.json({ teams: TEAMS })),
    http.get(`/api/v1/orgs/${slug}/identities`, () => HttpResponse.json({ identities: [] })),
    http.get(`/api/v1/orgs/${slug}/settings/coding/options`, () => HttpResponse.json(options(slug))),
  );
}

beforeEach(() => {
  seen = [];
  server.events.on('request:start', async ({ request }) => {
    const url = new URL(request.url);
    if (!url.pathname.endsWith('/settings/coding') && !url.pathname.endsWith('/settings/coding/options')) return;
    const body = request.method === 'PUT' ? await request.clone().json() : null;
    seen.push({ method: request.method, path: url.pathname, search: url.search, body });
  });
  stubShell(A);
  stubShell(B);
});
afterEach(() => { server.events.removeAllListeners('request:start'); });

function mount(route = `/orgs/${A}/settings/coding`, extra?: JSX.Element) {
  return renderGuarded(
    <>
      <Routes><Route path="/orgs/:slug/settings/*" element={<SettingsPage />} /></Routes>
      {extra}
    </>,
    { entries: [route] },
  );
}

const puts = () => seen.filter((s) => s.method === 'PUT');
const select = (name: RegExp) => screen.getByRole('combobox', { name });

describe('CodingSection — route, initial and populated states', () => {
  test('bookmarkable route renders the coding nav entry and the honest initial E0 state without writing', async () => {
    server.use(http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 0, 4, null))));
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-initial');
    const nav = screen.getByRole('complementary');
    expect(within(nav).getByRole('link', { name: 'Coding' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByTestId('coding-readiness')).toHaveTextContent('Coding execution is disabled in this release');
    expect(screen.getByTestId('coding-current')).toHaveTextContent('4');
    expect(screen.getByRole('radio', { name: /Disabled/ })).toBeChecked();
    await user.click(screen.getByTestId('coding-save'));
    expect(screen.getByRole('alert')).toHaveTextContent('Choose four different agents.');
    expect(puts()).toHaveLength(0);
  });

  test('E0 explicitly adopts current options and selects repository format before one complete first save', async () => {
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 0, 4, null))),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async ({ request }) => {
        const body = await request.json() as { operation_key: string; mapping: unknown };
        return HttpResponse.json({ format: 'workflow-coding-settings-save@1', org_slug: A,
          operation: receipt(body.operation_key, 1), current: current(1, 5), configuration: body.mapping });
      }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-initial');
    await user.selectOptions(select(/Developer/), 'dev_agent');
    await user.selectOptions(select(/Independent reviewer/), 'code_reviewer');
    await user.selectOptions(select(/^QA$/), 'qa_engineer');
    await user.selectOptions(select(/Agent manager/), 'engineering_manager');
    await user.click(screen.getByTestId('coding-save'));
    expect(puts()).toHaveLength(0);
    await user.click(screen.getByTestId('coding-adopt-options'));
    await user.click(screen.getByRole('checkbox', { name: 'happyranch' }));
    await user.click(screen.getByTestId('coding-save'));
    expect(puts()).toHaveLength(0); // Selecting a repository never guesses its format.
    await user.selectOptions(select(/Object format.*happyranch/), 'sha256');
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-saved');
    expect(puts()).toHaveLength(1);
    const expected = configuration();
    expect(puts()[0].body).toEqual({ format: 'workflow-coding-settings-command@1', org_slug: A,
      operation_key: expect.stringMatching(/^coding-settings:/), expected_generation: 0,
      expected_mapping_generation: 4, admission: 'disabled', mapping: { ...expected,
        eligible_replacements: { implementer: [], code_reviewer: [], qa_engineer: [], agent_manager: [] },
        repository_references: [{ ...options().repository_choices[0], object_format: 'sha256' }] } });
  });

  test('a refused read is a visible refusal, never a default E0 view', async () => {
    server.use(http.get(`/api/v1/orgs/${A}/settings/coding`, () =>
      HttpResponse.json({ detail: { code: 'coding_settings_invalid' } }, { status: 503 })));
    mount();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load coding settings.');
    expect(alert).toHaveTextContent('coding_settings_invalid');
    expect(screen.queryByTestId('coding-current')).not.toBeInTheDocument();
  });

  test('saves one complete canonical command with both CAS values and a cross-team developer; double click sends once', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    server.use(
      // code_reviewer has since moved teams: a changed mapping uses its current team.
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => {
        const saved = configuration();
        saved.roles.code_reviewer = { principal: 'code_reviewer', team: 'former-team' };
        return HttpResponse.json(view(A, 3, 6, saved));
      }),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async ({ request }) => {
        await gate;
        const body = await request.json() as { operation_key: string; mapping: unknown; admission: string };
        return HttpResponse.json({ format: 'workflow-coding-settings-save@1', org_slug: A,
          operation: receipt(body.operation_key, 4), current: current(4, 7), configuration: body.mapping });
      }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    await user.click(screen.getByTestId('coding-save'));
    await user.click(screen.getByTestId('coding-save'));
    // F04: while the frozen save is pending, fields are locked in render AND
    // in the handler; attempted role/admission edits are not taken.
    await waitFor(() => expect(puts()).toHaveLength(1));
    const key = (puts()[0].body as { operation_key: string }).operation_key;
    fireEvent.change(select(/Developer/), { target: { value: 'dev_agent' } });
    fireEvent.click(screen.getByRole('radio', { name: /Disabled/ }));
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(screen.getByRole('radio', { name: /Allowed/ })).toBeChecked();
    expect(select(/Developer/)).toBeDisabled();
    expect(screen.getByRole('radio', { name: /Disabled/ })).toBeDisabled();
    expect(screen.getByTestId('coding-sending')).toHaveTextContent(key);
    act(() => release());
    await screen.findByTestId('coding-saved');
    expect(puts()).toHaveLength(1);
    expect(screen.queryByTestId('coding-sending')).not.toBeInTheDocument();
    expect(select(/Developer/)).toBeEnabled();
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(screen.getByRole('radio', { name: /Allowed/ })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Discard changes' })).toHaveAttribute('aria-disabled', 'true');
    const body = puts()[0].body as Record<string, unknown>;
    const expected = configuration();
    expected.roles.implementer = { principal: 'frontend_engineer', team: 'design' };
    expected.roles.code_reviewer = { principal: 'code_reviewer', team: 'engineering' };
    expect(body).toEqual({
      format: 'workflow-coding-settings-command@1', org_slug: A, operation_key: expect.stringMatching(/^coding-settings:[0-9a-f-]{36}$/),
      expected_generation: 3, expected_mapping_generation: 6, admission: 'explicit', mapping: expected,
    });
    expect(screen.getByTestId('coding-saved')).toHaveTextContent('Receipt generation 4; current generation 4, mapping generation 7.');
  });

  test('disabled-retain sends the last accepted mapping exactly, even when an actor left the roster', async () => {
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 2, 5, configuration('retired_dev')))),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async ({ request }) => {
        const body = await request.json() as { operation_key: string; mapping: unknown };
        return HttpResponse.json({ format: 'workflow-coding-settings-save@1', org_slug: A,
          operation: receipt(body.operation_key, 3), current: current(3, 6, 'disabled'), configuration: body.mapping });
      }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    expect(select(/Developer/)).toHaveValue('retired_dev');
    expect(screen.getByText(/Not in the current agent list/)).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: /Disabled/ }));
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-saved');
    expect(puts()).toHaveLength(1);
    expect((puts()[0].body as { admission: string }).admission).toBe('disabled');
    expect((puts()[0].body as { mapping: unknown }).mapping).toEqual(configuration('retired_dev'));
  });

  test('four distinct principals are required before any send', async () => {
    server.use(http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 1, 1, configuration()))));
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Independent reviewer/), 'dev_agent');
    await user.click(screen.getByTestId('coding-save'));
    expect(screen.getByRole('alert')).toHaveTextContent('Choose four different agents.');
    expect(puts()).toHaveLength(0);
  });
});

describe('CodingSection — conflict, uncertainty and reconciliation', () => {
  test('409 keeps the draft and focus; a second save needs explicit adoption and a new key', async () => {
    let gen = 3;
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, gen, gen + 3, configuration()))),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async ({ request }) => {
        const body = await request.json() as { expected_generation: number; operation_key: string; mapping: unknown };
        if (body.expected_generation !== 5) {
          gen = 5;
          return HttpResponse.json({ detail: { code: 'coding_settings_generation_stale', current: current(5, 8) } }, { status: 409 });
        }
        return HttpResponse.json({ format: 'workflow-coding-settings-save@1', org_slug: A,
          operation: receipt(body.operation_key, 6), current: current(6, 9), configuration: body.mapping });
      }),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    const save = screen.getByTestId('coding-save');
    await user.click(save);
    await screen.findByTestId('coding-conflict');
    expect(screen.getByTestId('coding-conflict')).toHaveTextContent('now generation 5, mapping generation 8');
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(save).toHaveFocus();
    await waitFor(() => expect(screen.getByTestId('coding-current')).toHaveTextContent('5'));
    await user.click(save);
    expect(screen.getByText('Use the current version before saving again.')).toBeInTheDocument();
    expect(puts()).toHaveLength(1);
    await user.click(within(screen.getByTestId('coding-conflict')).getByRole('button', { name: 'Use the current version for my changes' }));
    await user.click(save);
    await screen.findByTestId('coding-saved');
    expect(puts()).toHaveLength(2);
    const [first, second] = puts().map((p) => p.body as { operation_key: string; expected_generation: number; expected_mapping_generation: number });
    expect(second.operation_key).not.toBe(first.operation_key);
    expect([second.expected_generation, second.expected_mapping_generation]).toEqual([5, 8]);
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
  });

  test('uncertain send is never resent; the original key is read back and receipt/current stay distinct', async () => {
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, ({ request }) => {
        const key = new URL(request.url).searchParams.get('operation_key');
        if (key) return HttpResponse.json({ ...view(A, 2, 9, configuration()), operation: receipt(key, 1) });
        return HttpResponse.json(view(A, 1, 1, configuration()));
      }),
      http.put(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.click(screen.getByRole('radio', { name: /Allowed/ }));
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-uncertain');
    await user.click(screen.getByTestId('coding-save'));
    expect(puts()).toHaveLength(1);
    const key = (puts()[0].body as { operation_key: string }).operation_key;
    expect(screen.getByTestId('coding-uncertain')).toHaveTextContent(key);
    await user.click(screen.getByRole('button', { name: 'Check original save' }));
    await screen.findByText('The original save was committed: receipt generation 1; current generation 2.');
    const reads = seen.filter((s) => s.method === 'GET' && s.search);
    expect(reads.map((r) => new URLSearchParams(r.search).get('operation_key'))).toEqual([key]);
    expect(puts()).toHaveLength(1);
  });

  test('unknown original key (404) is reported without granting a new write', async () => {
    let checks = 0;
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, ({ request }) => {
        if (!new URL(request.url).searchParams.get('operation_key')) return HttpResponse.json(view(A, 1, 1, configuration()));
        checks += 1;
        return checks === 2
          ? HttpResponse.json({ detail: { code: 'coding_settings_unavailable' } }, { status: 500 })
          : HttpResponse.json({ detail: { code: 'coding_settings_operation_not_found' } }, { status: 404 });
      }),
      http.put(`/api/v1/orgs/${A}/settings/coding`, () =>
        HttpResponse.json({ detail: { code: 'coding_settings_publication_uncertain', operation_key: 'k' } }, { status: 503 })),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.click(screen.getByRole('radio', { name: /Disabled/ }));
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-uncertain');
    await user.click(screen.getByRole('button', { name: 'Check original save' }));
    await screen.findByText(/No committed save with this key was found/);
    const key = (puts()[0].body as { operation_key: string; admission: string }).operation_key;
    // F01: an absent key releases nothing. Drive the former "Edit again"
    // release action whenever it is rendered, then an edit and a Save: no
    // second PUT and no fresh key may follow.
    const formerRelease = screen.queryByRole('button', { name: 'Edit again' });
    if (formerRelease) await user.click(formerRelease);
    fireEvent.click(screen.getByRole('radio', { name: /Allowed/ }));
    await user.click(screen.getByTestId('coding-save'));
    await act(() => delay(20));
    expect(puts()).toHaveLength(1);
    expect(formerRelease).toBeNull();
    expect(screen.getByRole('radio', { name: /Allowed/ })).toBeDisabled();
    expect(screen.getByTestId('coding-uncertain')).toHaveTextContent(key);
    // A failed check and a repeated explicit check stay read-only on the SAME key.
    await user.click(screen.getByRole('button', { name: 'Check original save' }));
    await screen.findByText(/The check failed \(coding_settings_unavailable\)/);
    await user.click(screen.getByTestId('coding-save'));
    await user.click(screen.getByRole('button', { name: 'Check original save' }));
    await screen.findByText(/No committed save with this key was found/);
    await user.click(screen.getByTestId('coding-save'));
    expect(screen.queryByTestId('coding-saved')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Disabled/ })).toBeChecked();
    const reads = seen.filter((r) => r.method === 'GET' && r.search);
    expect(reads.map((r) => new URLSearchParams(r.search).get('operation_key'))).toEqual([key, key, key]);
    expect(puts()).toHaveLength(1);
    expect((puts()[0].body as { admission: string }).admission).toBe('disabled');
  });
});

describe('CodingSection — ordering, org isolation and locale', () => {
  test.each(['release', 'authority', 'repositories', 'foreign', 'unsafe', 'error'] as const)(
    'E0 options %s is categorical and cannot send a PUT', async (missing) => {
      server.use(
        http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 0, 3, null))),
        http.get(`/api/v1/orgs/${A}/settings/coding/options`, () => {
          const value = options();
          if (missing === 'error') return HttpResponse.json({ detail: { code: 'coding_settings_invalid' } }, { status: 503 });
          return HttpResponse.json({ ...value,
            ...(missing === 'release' ? { release: null } : {}),
            ...(missing === 'authority' ? { authority: null } : {}),
            ...(missing === 'repositories' ? { repository_choices: [] } : {}),
            ...(missing === 'foreign' ? { org_slug: B } : {}),
            ...(missing === 'unsafe' ? { authority: { ...value.authority, generation: 9007199254740992 } } : {}),
          });
        }),
      );
      const user = userEvent.setup();
      mount();
      await screen.findByTestId('coding-initial');
      await user.selectOptions(select(/Developer/), 'dev_agent');
      await user.selectOptions(select(/Independent reviewer/), 'code_reviewer');
      await user.selectOptions(select(/^QA$/), 'qa_engineer');
      await user.selectOptions(select(/Agent manager/), 'engineering_manager');
      await waitFor(() => expect(screen.getByTestId('coding-adopt-options')).toBeDisabled());
      expect(screen.getByTestId('coding-options')).toHaveTextContent(
        ['foreign', 'unsafe', 'error'].includes(missing) ? 'Could not read current metadata' : 'Current imported release, authority and configured repository choices are required',
      );
      await user.click(screen.getByTestId('coding-save'));
      expect(puts()).toHaveLength(0);
    },
  );

  test('advanced authority stays outside an edited draft until explicit adoption; 409 keeps the new pin and never auto-sends', async () => {
    let releaseOptions!: () => void;
    const optionsGate = new Promise<void>((resolve) => { releaseOptions = resolve; });
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 3, 6, configuration()))),
      http.get(`/api/v1/orgs/${A}/settings/coding/options`, async () => { await optionsGate; return HttpResponse.json(options(A, 9)); }),
      http.put(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json({ detail: {
        code: 'coding_settings_generation_stale', current: current(5, 8),
      } }, { status: 409 })),
    );
    const user = userEvent.setup();
    const { client } = mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    select(/Developer/).focus();
    act(() => releaseOptions());
    await waitFor(() => expect(screen.getByTestId('coding-adopt-options')).toBeEnabled());
    expect(screen.getByTestId('coding-options')).toHaveTextContent('Draft authority: generation 7');
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(select(/Developer/)).toHaveFocus();
    await user.click(screen.getByTestId('coding-save'));
    expect(puts()).toHaveLength(0);
    await user.click(screen.getByTestId('coding-adopt-options'));
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-conflict');
    expect(puts()).toHaveLength(1);
    expect((puts()[0].body as { mapping: { authority: unknown } }).mapping.authority).toEqual(options(A, 9).authority);
    select(/Developer/).focus();
    await act(() => client.refetchQueries({ queryKey: ['settings', 'coding-options', A] }));
    expect(select(/Developer/)).toHaveFocus();
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(screen.getByTestId('coding-options')).toHaveTextContent('Draft authority: generation 9');
    expect(puts()).toHaveLength(1);
  });

  test('options revocation permits exact disabled-retain and cannot release an uncertain original save', async () => {
    let reads = 0;
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 3, 6, configuration('retired_dev')))),
      http.get(`/api/v1/orgs/${A}/settings/coding/options`, () => ++reads === 1
        ? HttpResponse.json(options())
        : HttpResponse.json({ detail: { code: 'coding_settings_invalid' } }, { status: 503 })),
      http.put(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.error()),
    );
    const user = userEvent.setup();
    mount();
    await screen.findByTestId('coding-current');
    await user.click(screen.getByRole('button', { name: 'Refresh configuration metadata' }));
    await screen.findByText('Could not read current metadata. Your draft and saved mapping are retained.');
    await user.click(screen.getByRole('radio', { name: /Disabled/ }));
    await user.click(screen.getByTestId('coding-save'));
    await screen.findByTestId('coding-uncertain');
    expect(puts()).toHaveLength(1);
    const original = puts()[0].body as { mapping: unknown; operation_key: string };
    expect(original.mapping).toEqual(configuration('retired_dev'));
    await user.click(screen.getByRole('button', { name: 'Refresh configuration metadata' }));
    await screen.findByText('Could not read current metadata. Your draft and saved mapping are retained.');
    fireEvent.click(screen.getByTestId('coding-adopt-options'));
    await user.click(screen.getByTestId('coding-save'));
    expect(screen.getByTestId('coding-uncertain')).toHaveTextContent(original.operation_key);
    expect(puts()).toHaveLength(1);
    expect(select(/Developer/)).toBeDisabled();
  });

  test('a late org-A options read never supplies org B metadata or changes its draft', async () => {
    let releaseA!: () => void;
    const gate = new Promise<void>((resolve) => { releaseA = resolve; });
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 0, 4, null))),
      http.get(`/api/v1/orgs/${B}/settings/coding`, () => HttpResponse.json(view(B, 0, 8, null))),
      http.get(`/api/v1/orgs/${A}/settings/coding/options`, async () => { await gate; return HttpResponse.json(options(A, 99)); }),
    );
    const user = userEvent.setup();
    const { router, client } = mount();
    await screen.findByTestId('coding-initial');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    await act(() => router.navigate(`/orgs/${B}/settings/coding`));
    await screen.findByTestId('coding-initial');
    act(() => releaseA());
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(screen.getByTestId('coding-options')).toHaveTextContent('Current authority: generation 7');
    expect(screen.getByTestId('coding-options')).not.toHaveTextContent('99');
    expect(select(/Developer/)).toHaveValue('');
    expect(puts()).toHaveLength(0);
  });

  test.each(['resolves', 'rejects'] as const)('a same-org read that %s after an accepted save cannot replace or error it', async (settles) => {
    let gets = 0;
    let releasePut!: () => void;
    let releaseGet!: () => void;
    const putGate = new Promise<void>((r) => { releasePut = r; });
    const getGate = new Promise<void>((r) => { releaseGet = r; });
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, async () => {
        gets += 1;
        if (gets > 1) {
          await getGate;
          // F03: an obsolete read that REJECTS must not hide the accepted form.
          if (settles === 'rejects') return HttpResponse.json({ detail: { code: 'coding_settings_unavailable' } }, { status: 503 });
        }
        return HttpResponse.json(view(A, 1, 1, configuration()));
      }),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async ({ request }) => {
        await putGate;
        const body = await request.json() as { operation_key: string; mapping: unknown };
        return HttpResponse.json({ format: 'workflow-coding-settings-save@1', org_slug: A,
          operation: receipt(body.operation_key, 2), current: current(2, 2, 'disabled'), configuration: body.mapping });
      }),
    );
    const user = userEvent.setup();
    const { client } = mount();
    await screen.findByTestId('coding-current');
    await user.click(screen.getByRole('radio', { name: /Disabled/ }));
    await user.click(screen.getByTestId('coding-save'));
    // A read issued while the write is outstanding.
    void client.refetchQueries({ queryKey: ['settings', 'coding', A] });
    await waitFor(() => expect(gets).toBe(2));
    act(() => releasePut());
    await screen.findByTestId('coding-saved');
    act(() => releaseGet());
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(within(screen.getByTestId('coding-current')).getAllByText('2').length).toBeGreaterThan(0);
    expect(screen.getByTestId('coding-current')).toHaveTextContent('Disabled');
    expect(screen.queryByText('Could not load coding settings.')).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Disabled/ })).toBeChecked();
    expect(screen.getByTestId('coding-saved')).toBeInTheDocument();
    expect(puts()).toHaveLength(1);
  });

  test('switching org A→B isolates views and drafts; a late org-A reply never reaches org B', async () => {
    let releaseA!: () => void;
    const gateA = new Promise<void>((r) => { releaseA = r; });
    let aReads = 0;
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, async () => {
        aReads += 1;
        if (aReads > 1) await gateA;
        return HttpResponse.json(view(A, 1, 1, configuration()));
      }),
      http.get(`/api/v1/orgs/${B}/settings/coding`, () => HttpResponse.json(view(B, 0, 0, null))),
    );
    const user = userEvent.setup();
    const { router, client } = mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    void client.refetchQueries({ queryKey: ['settings', 'coding', A] });
    await waitFor(() => expect(aReads).toBe(2));
    await act(() => router.navigate(`/orgs/${B}/settings/coding`));
    await screen.findByTestId('coding-initial');
    act(() => releaseA());
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(screen.getByTestId('coding-initial')).toBeInTheDocument();
    expect(select(/Developer/)).toHaveValue('');
  });

  test('send ownership survives leaving org A during the PUT and after uncertain settlement; org B stays isolated', async () => {
    let releasePut!: () => void;
    const putGate = new Promise<void>((r) => { releasePut = r; });
    server.use(
      http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 1, 1, configuration()))),
      http.get(`/api/v1/orgs/${B}/settings/coding`, () => HttpResponse.json(view(B, 0, 0, null))),
      http.put(`/api/v1/orgs/${A}/settings/coding`, async () => { await putGate; return HttpResponse.error(); }),
    );
    const user = userEvent.setup();
    const { router } = mount();
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    await user.click(screen.getByTestId('coding-save'));
    await waitFor(() => expect(puts()).toHaveLength(1));
    const sent = puts()[0].body as { operation_key: string; mapping: { roles: { implementer: { principal: string } } } };
    // Away to org B and back while the PUT is still pending.
    await act(() => router.navigate(`/orgs/${B}/settings/coding`));
    await screen.findByTestId('coding-initial');
    expect(screen.queryByTestId('coding-sending')).not.toBeInTheDocument();
    expect(select(/Developer/)).toBeEnabled();
    expect(select(/Developer/)).toHaveValue('');
    await act(() => router.navigate(`/orgs/${A}/settings/coding`));
    await screen.findByTestId('coding-current');
    await user.click(screen.getByTestId('coding-save'));
    await act(() => delay(20));
    expect(puts()).toHaveLength(1);
    expect(select(/Developer/)).toBeDisabled();
    expect(screen.getByTestId('coding-sending')).toHaveTextContent(sent.operation_key);
    // Away again; the pending send settles uncertain while A's pane is detached.
    await act(() => router.navigate(`/orgs/${A}/settings/preferences`));
    act(() => releasePut());
    await act(() => delay(20));
    await act(() => router.navigate(`/orgs/${B}/settings/coding`));
    await screen.findByTestId('coding-initial');
    expect(screen.queryByTestId('coding-uncertain')).not.toBeInTheDocument();
    await act(() => router.navigate(`/orgs/${A}/settings/coding`));
    await screen.findByTestId('coding-current');
    await user.click(screen.getByTestId('coding-save'));
    await act(() => delay(20));
    expect(puts()).toHaveLength(1);
    expect(select(/Developer/)).toBeDisabled();
    expect(screen.getByTestId('coding-uncertain')).toHaveTextContent(sent.operation_key);
    expect(sent.mapping.roles.implementer.principal).toBe('frontend_engineer');
    expect(puts().every((p) => p.path === `/api/v1/orgs/${A}/settings/coding`)).toBe(true);
  });

  test('en↔zh-CN preserves draft and focus with no coding request', async () => {
    server.use(http.get(`/api/v1/orgs/${A}/settings/coding`, () => HttpResponse.json(view(A, 1, 1, configuration()))));
    const user = userEvent.setup();
    mount(undefined, <><LocaleTestSwitch to="zh-CN" /><LocaleTestSwitch to="en" /></>);
    await screen.findByTestId('coding-current');
    await user.selectOptions(select(/Developer/), 'frontend_engineer');
    select(/Developer/).focus();
    const before = seen.length;
    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    expect(await screen.findByRole('combobox', { name: /开发者/ })).toHaveValue('frontend_engineer');
    expect(screen.getByRole('combobox', { name: /开发者/ })).toHaveFocus();
    expect(screen.getByTestId('coding-readiness')).toHaveTextContent('此版本中编码执行已停用');
    act(() => screen.getByTestId('test-set-locale-en').click());
    expect(select(/Developer/)).toHaveValue('frontend_engineer');
    expect(seen.length).toBe(before);
    await delay(20);
    expect(seen.length).toBe(before);
  });
});
