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
  present: boolean;
  path: string | null;
}

const PROFILE_A: Profile = {
  name: 'my-runner',
  command: 'my-runner-cli',
  adapter: 'claude',
  present: true,
  path: '/usr/local/bin/my-runner-cli',
};
const PROFILE_B: Profile = {
  name: 'ghost-cli',
  command: 'ghost',
  adapter: 'codex',
  present: false,
  path: null,
};

/** Static list stub. */
function stubProfiles(profiles: Profile[]) {
  server.use(
    http.get('/api/v1/executors/runtime/profiles', () =>
      HttpResponse.json({ profiles }),
    ),
  );
}

function render() {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<CustomProfilesSection />);
}

describe('CustomProfilesSection (Settings → Executors → custom CLIs)', () => {
  test('empty: renders the empty state, no rows', async () => {
    stubProfiles([]);
    render();

    expect(await screen.findByTestId('custom-profiles-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('custom-profile-rows')).not.toBeInTheDocument();
  });

  test('populated: one row per profile with name, executable, and present/path health', async () => {
    stubProfiles([PROFILE_A, PROFILE_B]);
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
    let store: Profile[] = [PROFILE_A, PROFILE_B];
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
    let store: Profile[] = [PROFILE_A];
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
    render();

    expect(
      await screen.findByText(/could not load custom executor profiles/i),
    ).toBeInTheDocument();
  });
});
