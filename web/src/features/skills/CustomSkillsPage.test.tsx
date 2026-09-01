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
  retired_at: string | null;
  validation_state: string;
  state?: string;
  purge_id?: string;
  purged_at?: string;
  content_hash?: string;
  hidden_reason?: string | null;
  skill_md_cache?: string | null;
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

  test('switches to an isolated removed catalog with count, badge, deep link, and empty state', async () => {
    const user = userEvent.setup();
    const requests: string[] = [];
    server.use(http.get(`${API}/catalog`, ({ request }) => {
      const view = new URL(request.url).searchParams.get('view');
      requests.push(view ?? 'current');
      return HttpResponse.json({ skills: view === 'removed' ? [{
        ...skill,
        state: 'permanently_removed',
        retired_at: '2026-08-30T00:00:00Z',
        purged_at: '2026-08-30T01:02:03Z',
        purge_id: 'purge:fixed',
        physical_erasure: false,
      }] : [skill] });
    }));
    mount(`/orgs/${SLUG}/skills/custom`);
    expect(await screen.findByText('Partner playbook')).toBeInTheDocument();
    expect(screen.getByText('Founder workspace · 1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Add custom skill' })).toHaveAttribute(
      'href', `/orgs/${SLUG}/skills/custom/new`,
    );

    await user.click(screen.getByRole('button', { name: 'Removed' }));
    expect(await screen.findByText('Permanently removed')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Add custom skill' })).not.toBeInTheDocument();
    expect(screen.getByText('Removed · 1')).toBeInTheDocument();
    expect(screen.getByText('Reservation retained')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View permanently removed playbook' })).toHaveAttribute(
      'href', `/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`,
    );
    expect(screen.queryByText('Retired')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retire/i })).not.toBeInTheDocument();
    expect(requests).toEqual(['current', 'removed']);

    await user.click(screen.getByRole('button', { name: 'Current' }));
    expect(await screen.findByText('Founder workspace · 1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Add custom skill' })).toBeInTheDocument();
    expect(requests).toEqual(['current', 'removed']);

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    server.use(http.get(`${API}/catalog`, ({ request }) => HttpResponse.json({
      skills: new URL(request.url).searchParams.get('view') === 'removed' ? [] : [skill],
    })));
    mount(`/orgs/${SLUG}/skills/custom`);
    await user.click(await screen.findByRole('button', { name: 'Removed' }));
    expect(await screen.findByText('No permanently removed skills')).toBeInTheDocument();
  });

  test('keeps the removed control selected through loading, error, and forbidden responses', async () => {
    const user = userEvent.setup();
    let removedResponse: 'loading' | 'error' | 'forbidden' = 'loading';
    server.use(http.get(`${API}/catalog`, ({ request }) => {
      if (new URL(request.url).searchParams.get('view') !== 'removed') return HttpResponse.json({ skills: [skill] });
      if (removedResponse === 'loading') return new Promise(() => {});
      return new HttpResponse(removedResponse, { status: removedResponse === 'forbidden' ? 403 : 500 });
    }));
    mount(`/orgs/${SLUG}/skills/custom`);
    await user.click(await screen.findByRole('button', { name: 'Removed' }));
    expect(screen.getByRole('button', { name: 'Removed' })).toHaveAttribute('aria-pressed', 'true');
    expect(await screen.findByLabelText('Loading removed custom skills')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    removedResponse = 'error';
    server.use(http.get(`${API}/catalog`, ({ request }) => new URL(request.url).searchParams.get('view') === 'removed' ? new HttpResponse('error', { status: 500 }) : HttpResponse.json({ skills: [skill] })));
    mount(`/orgs/${SLUG}/skills/custom`);
    await user.click(await screen.findByRole('button', { name: 'Removed' }));
    expect(await screen.findByText('Could not load removed skills')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    removedResponse = 'forbidden';
    server.use(http.get(`${API}/catalog`, ({ request }) => new URL(request.url).searchParams.get('view') === 'removed' ? new HttpResponse('forbidden', { status: 403 }) : HttpResponse.json({ skills: [skill] })));
    mount(`/orgs/${SLUG}/skills/custom`);
    await user.click(await screen.findByRole('button', { name: 'Removed' }));
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

  test('create explains a permanently reserved removed slug', async () => {
    const user = userEvent.setup();
    server.use(http.post(API, () => HttpResponse.json(
      { detail: { code: 'slug_permanently_reserved', detail: 'slug_permanently_reserved' } },
      { status: 409 },
    )));
    mount(`/orgs/${SLUG}/skills/custom/new`);
    await user.type(screen.getByLabelText('Name'), 'Removed guidance');
    await user.type(screen.getByLabelText('Slug'), 'playbook');
    await user.type(screen.getByLabelText('SKILL.md'), '# Removed guidance');
    await user.click(screen.getByRole('button', { name: 'Create custom skill' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This slug is permanently reserved by a removed custom skill. Open the Removed view to inspect its receipt.',
    );
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

  test('permanent removal requires the exact slug, exposes pending/error, and renders the tombstone', async () => {
    const user = userEvent.setup();
    const retired = { ...skill, retired_at: '2026-08-30T00:00:00Z' };
    let release: ((value: Response) => void) | undefined;
    server.use(
      http.get(`${API}/${encodeURIComponent(SKILL_ID)}`, () => HttpResponse.json(retired)),
      http.post(`${API}/${encodeURIComponent(SKILL_ID)}/purge`, () => new Promise<Response>((resolve) => { release = resolve; })),
    );
    mount(`/orgs/${SLUG}/skills/custom/${encodeURIComponent(SKILL_ID)}`);
    await user.click(await screen.findByRole('button', { name: 'Permanently remove' }));
    const confirm = screen.getAllByRole('button', { name: 'Permanently remove' })[1];
    expect(confirm).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: /Type playbook to confirm/ }), 'playbook');
    await user.click(confirm);
    expect(await screen.findByRole('button', { name: 'Removing…' })).toBeDisabled();
    release?.(new HttpResponse('failed', { status: 500 }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Permanent removal failed.');

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    mountDetail({ skill: {
      ...retired,
      state: 'permanently_removed',
      purge_id: 'purge:fixed',
      purged_at: '2026-08-30T01:02:03Z',
    } });
    expect(await screen.findByRole('heading', { name: 'Permanently removed' })).toBeInTheDocument();
    expect(screen.getByText('purge:fixed')).toBeInTheDocument();
    expect(screen.getByText(/physical_erasure=false/)).toBeInTheDocument();
  });

  test('detail renders an agent-created current guidance source without populating Add version', async () => {
    const user = userEvent.setup();
    const source = '# Partner guidance\n\nUse <script>unsafe()</script> literally.\n  Keep this indentation.';
    mountDetail({ skill: { ...skill, content_hash: '4d0fc04d9c0d89a12a4c8fb914a92f7281fb8d3ba14f301d50b0af3beaaf83f7', skill_md_cache: source } });

    const guidance = await screen.findByRole('region', { name: 'Current guidance / SKILL.md' });
    expect(guidance).toHaveTextContent('Current version: v1');
    expect(guidance).toHaveTextContent('Content hash: 4d0fc04d9c0d89a12a4c8fb914a92f7281fb8d3ba14f301d50b0af3beaaf83f7');
    expect(guidance.querySelector('pre')?.textContent).toBe(source);
    expect(guidance.querySelector('script')).not.toBeInTheDocument();
    const addVersion = screen.getByPlaceholderText('SKILL.md content');
    expect(addVersion).toHaveValue('');
    await user.type(addVersion, '# Proposed version');
    expect(addVersion).toHaveValue('# Proposed version');
    expect(guidance.querySelector('pre')?.textContent).toBe(source);
  });

  test('detail safely explains null or absent legacy current guidance', async () => {
    mountDetail({ skill: { ...skill, skill_md_cache: null } });
    expect(await screen.findByText('No current SKILL.md content is available.')).toBeInTheDocument();

    cleanup();
    server.resetHandlers();
    installShellHandlers();
    mountDetail();
    expect(await screen.findByText('No current SKILL.md content is available.')).toBeInTheDocument();
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
