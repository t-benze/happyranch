import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { OnboardingPage } from './OnboardingPage';
import { health as healthApi, orgs as orgsApi } from '@/lib/api';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/onboarding']}>
        <OnboardingPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  // Default: a healthy, empty container (no orgs, none broken).
  vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({ orgs: [], broken: [] });
  // Default prereqs: all present (compact success line).
  vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
    prereqs: [
      { tool: 'claude', present: true, path: '/usr/bin/claude', hint: 'Install Claude Code' },
      { tool: 'codex', present: true, path: '/usr/bin/codex', hint: 'Install Codex' },
      { tool: 'opencode', present: true, path: '/usr/bin/opencode', hint: 'Install opencode' },
      { tool: 'pi', present: true, path: '/usr/bin/pi', hint: 'Install Pi' },
    ],
  });
});

describe('OnboardingPage', () => {
  test('welcome step advances to the create form', async () => {
    const user = userEvent.setup();
    renderPage();

    expect(
      screen.getByRole('heading', { name: /welcome to happyranch/i }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    expect(screen.getByLabelText(/slug/i)).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: /name your org/i }),
    ).toBeInTheDocument();
  });

  test('Create org disabled until slug matches ^[a-z0-9-]{1,40}$', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    const input = screen.getByLabelText(/slug/i);
    const submit = screen.getByRole('button', { name: /^create org$/i });
    expect(submit).toBeDisabled();

    await user.type(input, 'Bad_Slug');
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'good-slug-1');
    expect(submit).not.toBeDisabled();
  });

  test('creating an org shows the distinct creating state, then the success step', async () => {
    const user = userEvent.setup();
    // Controllable promise so we can observe the interim `creating` state.
    let resolveCreate: (v: { slug: string }) => void = () => {};
    vi.spyOn(orgsApi, 'createOrg').mockReturnValue(
      new Promise((res) => {
        resolveCreate = res;
      }),
    );
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await user.type(screen.getByLabelText(/slug/i), 'good-slug');
    await user.click(screen.getByRole('button', { name: /^create org$/i }));

    // Distinct creating progress state — not just a relabeled button.
    expect(await screen.findByLabelText('Creating org')).toBeInTheDocument();
    expect(screen.getByText(/creating/i)).toBeInTheDocument();
    expect(screen.getByText(/setting up the workspace/i)).toBeInTheDocument();

    resolveCreate({ slug: 'good-slug' });

    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /good-slug.*is ready/i }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole('button', { name: /enter good-slug/i }),
    ).toBeInTheDocument();
  });

  test('createOrg posts the exact slug from the contract', async () => {
    const user = userEvent.setup();
    const spy = vi
      .spyOn(orgsApi, 'createOrg')
      .mockResolvedValue({ slug: 'good-slug' });
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await user.type(screen.getByLabelText(/slug/i), 'good-slug');
    await user.click(screen.getByRole('button', { name: /^create org$/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ slug: 'good-slug' }),
    );
  });

  test('surfaces 409 org_exists inline without leaving the create step', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }),
    );
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await user.type(screen.getByLabelText(/slug/i), 'taken');
    await user.click(screen.getByRole('button', { name: /^create org$/i }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i),
    );
    // Still on the create step (success heading never rendered).
    expect(
      screen.queryByRole('heading', { name: /is ready/i }),
    ).not.toBeInTheDocument();
  });

  test('broken orgs render read-only with their error, Copy-error, and NO retry', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({
      orgs: [],
      broken: [{ slug: 'busted-org', error: 'agents/ dir missing' }],
    });
    renderPage();

    await waitFor(() =>
      expect(screen.getByText('busted-org')).toBeInTheDocument(),
    );
    expect(screen.getByText(/agents\/ dir missing/i)).toBeInTheDocument();
    // The reassuring framing is preserved.
    expect(screen.getByText(/don.t block you/i)).toBeInTheDocument();
    // Retry is founder-gated (G12) — it must not be fabricated here.
    expect(
      screen.queryByRole('button', { name: /retry/i }),
    ).not.toBeInTheDocument();

    // Copy-error copies the raw error string and confirms.
    const copyBtn = screen.getByRole('button', { name: /copy error/i });
    await user.click(copyBtn);
    expect(writeText).toHaveBeenCalledWith('agents/ dir missing');
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  test('executor prereqs — checking affordance while the query is pending', async () => {
    const user = userEvent.setup();
    // Never resolves — the panel stays in its checking state.
    vi.spyOn(healthApi, 'getPrereqs').mockReturnValue(new Promise(() => {}));
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    expect(
      await screen.findByText(/checking host tools/i),
    ).toBeInTheDocument();
  });

  test('executor prereqs — all present shows the X-of-Y summary and resolved paths', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await waitFor(() =>
      expect(screen.getByLabelText('Executor readiness')).toBeInTheDocument(),
    );
    // FE-computed summary from the real array.
    expect(screen.getByText(/4 of 4/)).toBeInTheDocument();
    expect(screen.getByText(/tools present/)).toBeInTheDocument();
    // Resolved path is rendered (backend-real, previously unrendered).
    expect(screen.getByText('/usr/bin/claude')).toBeInTheDocument();
  });

  test('executor prereqs — missing executors show the hint and a missing pill', async () => {
    const user = userEvent.setup();
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: 'Install Claude Code' },
        { tool: 'pi', present: false, path: null, hint: 'Install Pi' },
      ],
    });
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await waitFor(() =>
      expect(screen.getByLabelText('Executor readiness')).toBeInTheDocument(),
    );
    // Summary reflects the partial count.
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
    // Absence surfaced with the hint + a missing pill.
    expect(screen.getByText(/Install Pi/)).toBeInTheDocument();
    expect(screen.getByText('missing')).toBeInTheDocument();
    // The present executor still shows, with its pill.
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(screen.getByText('present')).toBeInTheDocument();
  });

  test('executor prereqs — query error is silent (no panel rendered)', async () => {
    const user = userEvent.setup();
    vi.spyOn(healthApi, 'getPrereqs').mockRejectedValue(new Error('network'));
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    // The panel is absent once the query settles into error — a silent
    // degradation. The interim `checking` affordance clears after the
    // panel's single retry (~1s retryDelay), so allow for that window.
    await waitFor(
      () => {
        expect(
          screen.queryByLabelText('Executor readiness'),
        ).not.toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });
});
