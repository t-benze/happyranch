import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import { CustomProfilesSection } from './CustomProfilesSection';

interface Profile {
  name: string;
  command: string | null;
  adapter: string | null;
  workspace_adapter_id: string | null;
  command_adapter_id: string | null;
  present: boolean;
  path: string | null;
}

interface AdapterEntry {
  id: string;
  name: string;
  executable: string;
  executable_hash: string;
  version: string;
  capabilities: string[];
  contract_version: number;
  workspace_adapter: string;
  status: string;
  registered_at: string;
  registered_by: string;
  approved_at: string | null;
  approved_by: string | null;
  intended_profile_name: string | null;
  eligibility: string | null;
}

const PROFILE_GENERIC: Profile = {
  name: 'my-runner',
  command: 'my-runner-cli',
  adapter: 'claude',
  workspace_adapter_id: 'claude',
  command_adapter_id: 'generic-cli',
  present: true,
  path: '/usr/local/bin/my-runner-cli',
};

const PROFILE_GHOST: Profile = {
  name: 'ghost-cli',
  command: 'ghost',
  adapter: 'codex',
  workspace_adapter_id: 'codex',
  command_adapter_id: 'generic-cli',
  present: false,
  path: null,
};

const PROFILE_ADAPTER_BACKED: Profile = {
  name: 'adapter-cli',
  command: null,
  adapter: 'pi',
  workspace_adapter_id: 'pi',
  command_adapter_id: 'custom-adapter:approved-adapter',
  present: false,
  path: null,
};

const APPROVED_ADAPTER: AdapterEntry = {
  id: 'approved-adapter',
  name: 'approved-adapter',
  executable: '/opt/bin/approved-adapter',
  executable_hash: 'abc123',
  version: '1.0.0',
  capabilities: [],
  contract_version: 1,
  workspace_adapter: 'pi',
  status: 'approved',
  registered_at: '2026-07-31T00:00:00Z',
  registered_by: 'test',
  approved_at: '2026-07-31T01:00:00Z',
  approved_by: 'founder',
  intended_profile_name: 'adapter-cli',
  eligibility: 'already_bound',
};

/** Static profiles list stub. */
function stubProfiles(profiles: Profile[]) {
  server.use(
    http.get('/api/v1/executors/runtime/profiles', () =>
      HttpResponse.json({ profiles }),
    ),
  );
}

/** Static adapters list stub. */
function stubAdapters(adapters: AdapterEntry[]) {
  server.use(
    http.get('/api/v1/runtime/adapters', () => HttpResponse.json(adapters)),
  );
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<CustomProfilesSection />);
}

describe('CustomProfilesSection (Settings → Executors → custom CLIs)', () => {
  test('empty: renders the empty state, no rows', async () => {
    stubProfiles([]);
    stubAdapters([]);
    render();

    expect(await screen.findByTestId('custom-profiles-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('custom-profile-rows')).not.toBeInTheDocument();
  });

  test('populated: one row per profile with name, executable, and present/path health', async () => {
    stubProfiles([PROFILE_GENERIC, PROFILE_GHOST]);
    stubAdapters([]);
    render();

    const rowA = await screen.findByTestId('profile-row-my-runner');
    expect(within(rowA).getByText('my-runner')).toBeInTheDocument();
    expect(within(rowA).getByText('my-runner-cli')).toBeInTheDocument();
    // present === true → the /health/prereqs-style "on this machine" pill + path.
    expect(within(rowA).getByTestId('profile-health')).toHaveAttribute(
      'data-present',
      'true',
    );
    expect(within(rowA).getByText('/usr/local/bin/my-runner-cli')).toBeInTheDocument();

    const rowB = screen.getByTestId('profile-row-ghost-cli');
    // present === false → NOT on this machine (PATH alone is not present).
    expect(within(rowB).getByTestId('profile-health')).toHaveAttribute(
      'data-present',
      'false',
    );
    expect(screen.queryByTestId('custom-profiles-empty')).not.toBeInTheDocument();
  });

  test('remove: guarded confirm calls removeRuntimeProfile then refetches the list', async () => {
    const user = userEvent.setup();
    // Stateful store so the invalidation-driven refetch reflects the removal.
    let store: Profile[] = [PROFILE_GENERIC, PROFILE_GHOST];
    const deleted: string[] = [];
    server.use(
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ profiles: store }),
      ),
      http.delete('/api/v1/executors/runtime/profiles/:name', ({ params }) => {
        const name = String(params.name);
        deleted.push(name);
        store = store.filter((p) => p.name !== name);
        return HttpResponse.json({ name, removed: true });
      }),
    );
    stubAdapters([]);
    render();

    const rowA = await screen.findByTestId('profile-row-my-runner');
    // First click arms the confirm step (guarded, not immediate).
    await user.click(within(rowA).getByTestId('profile-remove-my-runner'));
    await user.click(screen.getByTestId('profile-confirm-remove-my-runner'));

    // The DELETE fired for that name...
    await screen.findByTestId('profile-row-ghost-cli');
    expect(deleted).toEqual(['my-runner']);
    // ...and the invalidation-driven refetch dropped the row while keeping the other.
    expect(screen.queryByTestId('profile-row-my-runner')).not.toBeInTheDocument();
    expect(screen.getByTestId('profile-row-ghost-cli')).toBeInTheDocument();
  });

  test('remove: a 404 (already gone) is handled gracefully — refetch, no error banner', async () => {
    const user = userEvent.setup();
    // The profile was concurrently removed: DELETE 404s AND the refetch now
    // returns an empty list.
    let store: Profile[] = [PROFILE_GENERIC];
    server.use(
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ profiles: store }),
      ),
      http.delete('/api/v1/executors/runtime/profiles/:name', () => {
        store = [];
        return HttpResponse.json(
          { detail: { code: 'not_found' } },
          { status: 404 },
        );
      }),
    );
    stubAdapters([]);
    render();

    const rowA = await screen.findByTestId('profile-row-my-runner');
    await user.click(within(rowA).getByTestId('profile-remove-my-runner'));
    await user.click(screen.getByTestId('profile-confirm-remove-my-runner'));

    // Graceful: the list refetches to empty, no opaque error surfaced.
    expect(await screen.findByTestId('custom-profiles-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-remove-error-my-runner')).not.toBeInTheDocument();
  });

  test('error: a failed list load surfaces an alert, not an opaque blank', async () => {
    server.use(
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    stubAdapters([]);
    render();

    expect(
      await screen.findByText(/could not load custom executor profiles/i),
    ).toBeInTheDocument();
  });

  /* ---- seq334: adapter-backed CLI rows join the approved adapter executable ---- */

  test('adapter-backed CLI row renders the approved adapter executable despite null profile.command', async () => {
    stubProfiles([PROFILE_ADAPTER_BACKED]);
    stubAdapters([APPROVED_ADAPTER]);
    render();

    const row = await screen.findByTestId('profile-row-adapter-cli');
    // The executable comes from the approved adapter entry, not profile.command.
    expect(within(row).getByText('/opt/bin/approved-adapter')).toBeInTheDocument();
    // Generic "No executable recorded" message must NOT appear.
    expect(within(row).queryByText(/No executable recorded/)).not.toBeInTheDocument();
    // Adapter implementation terms must not surface in ordinary Settings.
    expect(within(row).queryByText(/Command adapter:/i)).not.toBeInTheDocument();
    expect(within(row).queryByText(/Workspace adapter:/i)).not.toBeInTheDocument();
    expect(within(row).queryByText('approved-adapter')).not.toBeInTheDocument();
  });

  test('adapter-backed CLI row still shows workspace adapter text for generic rows', async () => {
    stubProfiles([PROFILE_GENERIC]);
    stubAdapters([]);
    render();

    const row = await screen.findByTestId('profile-row-my-runner');
    // Generic custom CLI presentation is unchanged.
    expect(within(row).getByText(/Workspace adapter:/i)).toBeInTheDocument();
    expect(within(row).getByText(/claude/i)).toBeInTheDocument();
  });

  /* ---- seq334: approved-unbound recovery lives in the Custom CLIs area ---- */

  test('THR-107 slice 3: no CLI-level recovery/bind affordance renders for any adapter eligibility (recovery UI removed)', async () => {
    stubProfiles([]);
    stubAdapters([
      { ...APPROVED_ADAPTER, eligibility: 'ready_to_bind' },
      { ...APPROVED_ADAPTER, id: 'no-intended-adapter', intended_profile_name: null, eligibility: 'recovery_ready' },
    ]);
    render();

    await screen.findByTestId('custom-profiles-empty');
    expect(screen.queryByTestId(/^cli-recovery-row-/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Finish connecting this CLI/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^bind /i })).not.toBeInTheDocument();
  });

  test('already_bound adapter does not render a recovery row when its profile is listed', async () => {
    stubProfiles([PROFILE_ADAPTER_BACKED]);
    stubAdapters([APPROVED_ADAPTER]);
    render();

    await screen.findByTestId('profile-row-adapter-cli');
    expect(screen.queryByTestId('cli-recovery-row-approved-adapter')).not.toBeInTheDocument();
  });
});
