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

  test('creating an org posts the slug and shows the success step', async () => {
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
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /organization created/i }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole('button', { name: /enter good-slug/i }),
    ).toBeInTheDocument();
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
      screen.queryByRole('heading', { name: /organization created/i }),
    ).not.toBeInTheDocument();
  });

  test('broken orgs render read-only with their error and NO retry action', async () => {
    vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({
      orgs: [],
      broken: [{ slug: 'busted-org', error: 'agents/ dir missing' }],
    });
    renderPage();

    await waitFor(() =>
      expect(screen.getByText('busted-org')).toBeInTheDocument(),
    );
    expect(screen.getByText(/agents\/ dir missing/i)).toBeInTheDocument();
    // Retry is founder-gated (G12) — it must not be fabricated here.
    expect(
      screen.queryByRole('button', { name: /retry/i }),
    ).not.toBeInTheDocument();
  });

  test('executor prereqs — all present shows compact success line', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await waitFor(() =>
      expect(
        screen.getByLabelText('Executor readiness'),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/all executor clis found on path/i),
    ).toBeInTheDocument();
  });

  test('executor prereqs — missing executors show warning panel with hint', async () => {
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
      expect(
        screen.getByLabelText('Executor readiness'),
      ).toBeInTheDocument(),
    );
    // Absence surfaced with the hint.
    expect(screen.getByText(/Install Pi/)).toBeInTheDocument();
    // Panel header shows missing count.
    expect(screen.getByText(/1 missing/)).toBeInTheDocument();
    // The present executor still shows.
    expect(screen.getByText(/claude/)).toBeInTheDocument();
  });

  test('executor prereqs — query error is silent (no panel rendered)', async () => {
    const user = userEvent.setup();
    vi.spyOn(healthApi, 'getPrereqs').mockRejectedValue(new Error('network'));
    renderPage();
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    // The panel is absent — this is a silent degradation.
    await waitFor(() => {
      expect(
        screen.queryByLabelText('Executor readiness'),
      ).not.toBeInTheDocument();
    });
  });
});
