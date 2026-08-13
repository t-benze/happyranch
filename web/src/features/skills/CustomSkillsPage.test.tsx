import { cleanup, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';
const API = `/api/v1/orgs/${SLUG}/custom-skills`;
const SKILL_ID = 'custom:playbook';

const skill: {
  skill_id: string;
  slug: string;
  name: string;
  description: string;
  current_version_id: number;
  retired_at: null;
  validation_state: string;
  hidden_reason?: string | null;
} = {
  skill_id: SKILL_ID,
  slug: 'playbook',
  name: 'Partner playbook',
  description: 'Founder-authored guidance.',
  current_version_id: 1,
  retired_at: null,
  validation_state: 'valid',
};

function mount(route: string): void {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<AppRoutes />, { route });
}

function mountDetail(options: { skill?: typeof skill; eligibility?: () => Response; preview?: (request: Request) => Response | Promise<Response>; save?: (request: Request) => Response | Promise<Response> } = {}): void {
  server.use(
    http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => HttpResponse.json(options.skill ?? skill)),
    http.get(`${API}/${encodeURIComponent(SKILL_ID)}/versions`, () => HttpResponse.json({ versions: [{ id: 1, content_hash: 'abc', created_at: '2026-08-01T00:00:00Z', validation_state: 'valid' }] })),
    http.get(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, () => options.eligibility?.() ?? HttpResponse.json({ rules: [{ scope_type: 'agent', scope_target: 'ada', effect: 'allow' }], revision: 1 })),
    http.post(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility/preview`, ({ request }) => options.preview?.(request) ?? HttpResponse.json({ newly_visible: [], newly_hidden: [], unchanged: [], revision: 1 })),
    http.put(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, ({ request }) => options.save?.(request) ?? HttpResponse.json({ newly_visible: [], newly_hidden: [], unchanged: [], revision: 1 })),
  );
  mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
}

function installShellHandlers(): void {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/tmp/alpha' }] })),
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({})),
    http.get(`${API}/${encodeURIComponent(SKILL_ID)}/versions`, () => HttpResponse.json({ versions: [] })),
    http.get(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, () => HttpResponse.json({ rules: [], revision: 1 })),
  );
}

beforeEach(installShellHandlers);

afterEach(() => sessionStorage.clear());

describe('Custom Skills routes', () => {
  test('list route has deterministic loading, empty, 5xx, and founder-forbidden states', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(http.get(`${API}/catalog`, () => new Promise(() => {})));
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/custom` });
    expect(document.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/catalog`, () => HttpResponse.json({ skills: [] })));
    mount(`/orgs/${SLUG}/skills/custom`);
    expect(await screen.findByText('No custom skills yet')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/catalog`, () => new HttpResponse('unavailable', { status: 500 })));
    mount(`/orgs/${SLUG}/skills/custom`);
    expect(await screen.findByText('Could not load custom skills')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/catalog`, () => new HttpResponse('forbidden', { status: 403 })));
    mount(`/orgs/${SLUG}/skills/custom`);
    expect(await screen.findByText('Founder access required')).toBeInTheDocument();
  });

  test('create route exposes mutation pending, error, and forbidden states', async () => {
    const user = userEvent.setup();
    server.use(http.post(API, () => new Promise(() => {})));
    mount(`/orgs/${SLUG}/skills/custom/new`);
    await user.type(screen.getByLabelText('Name'), 'New guidance');
    await user.type(screen.getByLabelText('Slug'), 'new-guidance');
    await user.type(screen.getByLabelText('SKILL.md'), '# New guidance');
    await user.click(screen.getByRole('button', { name: 'Create custom skill' }));
    expect(await screen.findByRole('button', { name: 'Creating…' })).toBeDisabled();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.post(API, () => new HttpResponse('unavailable', { status: 500 })));
    mount(`/orgs/${SLUG}/skills/custom/new`);
    await user.type(screen.getByLabelText('Name'), 'New guidance');
    await user.type(screen.getByLabelText('Slug'), 'new-guidance');
    await user.type(screen.getByLabelText('SKILL.md'), '# New guidance');
    await user.click(screen.getByRole('button', { name: 'Create custom skill' }));
    expect(await screen.findByText('Could not create this custom skill. Check the details and try again.')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.post(API, () => new HttpResponse('forbidden', { status: 403 })));
    mount(`/orgs/${SLUG}/skills/custom/new`);
    await user.type(screen.getByLabelText('Name'), 'Forbidden guidance');
    await user.type(screen.getByLabelText('Slug'), 'forbidden-guidance');
    await user.type(screen.getByLabelText('SKILL.md'), '# Forbidden');
    await user.click(screen.getByRole('button', { name: 'Create custom skill' }));
    expect(await screen.findByText('Founder access required')).toBeInTheDocument();
  });

  test('detail route has loading, 5xx, and founder-forbidden states', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => new Promise(() => {})));
    renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}` });
    expect(document.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => new HttpResponse('unavailable', { status: 500 })));
    mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
    expect(await screen.findByText('Could not load this custom skill')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => new HttpResponse('forbidden', { status: 403 })));
    mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
    expect(await screen.findByText('Founder access required')).toBeInTheDocument();
  });

  test('detail uses the hidden eligibility badge only when the server reports it', async () => {
    mountDetail({ skill: { ...skill, hidden_reason: 'no_eligibility_policy' } });
    expect(await screen.findByText('Hidden — eligibility not configured')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    mountDetail();
    expect(await screen.findByText('Validated')).toBeInTheDocument();
  });

  test('editor sends org rules with an explicit effect to preview and save', async () => {
    const user = userEvent.setup();
    const payloads: unknown[] = [];
    mountDetail({
      eligibility: () => HttpResponse.json({ rules: [], revision: 1 }),
      preview: async (request) => { payloads.push(await request.json()); return HttpResponse.json({ newly_visible: [], newly_hidden: [], unchanged: [], revision: 1 }); },
      save: async (request) => { payloads.push(await request.json()); return HttpResponse.json({ newly_visible: [], newly_hidden: [], unchanged: [], revision: 1 }); },
    });
    await user.click(await screen.findByRole('button', { name: 'Add rule' }));
    await user.selectOptions(screen.getByLabelText('Rule 1 scope'), 'org');
    await user.selectOptions(screen.getByLabelText('Rule 1 effect'), 'deny');
    await user.click(screen.getByRole('button', { name: 'Preview impact' }));
    await user.click(screen.getByRole('button', { name: 'Save eligibility' }));
    await waitFor(() => expect(payloads).toEqual([
      [{ scope_type: 'org', scope_target: '', effect: 'deny' }],
      [{ scope_type: 'org', scope_target: '', effect: 'deny' }],
    ]));
  });

  test('detail mutation error is visible and does not silently clear the edited rule', async () => {
    const user = userEvent.setup();
    mountDetail({ save: () => new HttpResponse('unavailable', { status: 500 }) });
    const target = await screen.findByLabelText('Rule 1 target');
    await user.clear(target);
    await user.type(target, 'local-draft');
    await user.click(screen.getByRole('button', { name: 'Save eligibility' }));
    expect(await screen.findByText('Could not save eligibility.')).toBeInTheDocument();
    expect(screen.getByLabelText('Rule 1 target')).toHaveValue('local-draft');
  });

  test('409 refetches only the baseline revision, preserves the local draft, and retries with the new If-Match', async () => {
    const user = userEvent.setup();
    let eligibilityReads = 0;
    const saveRevisions: string[] = [];
    server.use(
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => HttpResponse.json(skill)),
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}/versions`, () => HttpResponse.json({ versions: [] })),
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, () => {
        eligibilityReads += 1;
        return HttpResponse.json(eligibilityReads === 1
          ? { rules: [{ scope_type: 'agent', scope_target: 'server-original', effect: 'allow' }], revision: 1 }
          : { rules: [{ scope_type: 'team', scope_target: 'server-new', effect: 'allow' }], revision: 2 });
      }),
      http.put(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, async ({ request }) => {
        saveRevisions.push(request.headers.get('If-Match') ?? '');
        return saveRevisions.length === 1
          ? HttpResponse.json({ detail: { code: 'stale_revision' } }, { status: 409 })
          : HttpResponse.json({ newly_visible: [], newly_hidden: [], unchanged: [], revision: 2 });
      }),
    );
    mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
    const target = await screen.findByLabelText('Rule 1 target');
    await user.clear(target);
    await user.type(target, 'founder-draft');
    await user.click(screen.getByRole('button', { name: 'Save eligibility' }));
    expect(await screen.findByText(/Eligibility changed elsewhere/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Current revision: v2')).toBeInTheDocument());
    expect(screen.getByLabelText('Rule 1 target')).toHaveValue('founder-draft');

    await user.click(screen.getByRole('button', { name: 'Save eligibility' }));
    expect(await screen.findByText('Eligibility saved.')).toBeInTheDocument();
    expect(saveRevisions).toEqual(['1', '2']);
  });

  test('clearing a version selector disables the diff request instead of querying version zero', async () => {
    const user = userEvent.setup();
    let diffCalls = 0;
    server.use(
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => HttpResponse.json(skill)),
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}/versions`, () => HttpResponse.json({ versions: [{ id: 1, content_hash: 'a', created_at: '2026-08-01T00:00:00Z' }, { id: 2, content_hash: 'b', created_at: '2026-08-02T00:00:00Z' }] })),
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}/eligibility`, () => HttpResponse.json({ rules: [], revision: 1 })),
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}/versions/:a/diff/:b`, () => { diffCalls += 1; return HttpResponse.json({ a: {}, b: {}, diff: [] }); }),
    );
    mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
    const before = await screen.findByLabelText('Before');
    const after = screen.getByLabelText('After');
    await user.selectOptions(before, '1');
    await user.selectOptions(after, '2');
    await waitFor(() => expect(diffCalls).toBe(1));
    await user.selectOptions(before, '');
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(diffCalls).toBe(1);
  });
});
