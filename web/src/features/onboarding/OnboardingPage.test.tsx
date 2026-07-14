import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { UserEvent } from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { OnboardingPage } from './OnboardingPage';
import {
  health as healthApi,
  orgs as orgsApi,
  settings as settingsApi,
} from '@/lib/api';

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

/** First-run onboarding leads with Step 1 (connect a built-in agentic CLI).
 *  Org-flow tests skip past it to reach Step 2. */
async function skipConnect(user: UserEvent): Promise<void> {
  await user.click(await screen.findByRole('button', { name: /skip/i }));
}

/** Step 1 leads with the built-in dropdown; the custom copy-paste flow is a
 *  secondary affordance. Custom-flow tests switch into it first. */
async function goCustom(user: UserEvent): Promise<void> {
  await user.click(
    await screen.findByRole('button', { name: /connect a custom cli instead/i }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  // Default: a healthy, empty container (no orgs, none broken).
  vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({ orgs: [], broken: [] });
  // Default prereqs: all registered (compact success line).
  vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
    prereqs: [
      { tool: 'claude', present: true, path: '/usr/bin/claude', hint: 'Register Claude Code' },
      { tool: 'codex', present: true, path: '/usr/bin/codex', hint: 'Register Codex' },
      { tool: 'opencode', present: true, path: '/usr/bin/opencode', hint: 'Register opencode' },
      { tool: 'pi', present: true, path: '/usr/bin/pi', hint: 'Register Pi' },
    ],
  });
});

describe('OnboardingPage — Step 1 (connect a built-in agentic CLI)', () => {
  test('first run leads with the built-in connect step (dropdown)', async () => {
    renderPage();
    expect(
      await screen.findByRole('heading', { name: /connect your agentic cli/i }),
    ).toBeInTheDocument();
    // The primary affordance is a dropdown of built-in agentic CLIs.
    expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
  });

  test('Generate is disabled until a built-in kind is picked', async () => {
    const user = userEvent.setup();
    renderPage();
    const gen = await screen.findByRole('button', {
      name: /generate connect prompt/i,
    });
    expect(gen).toBeDisabled();

    await user.selectOptions(
      await screen.findByLabelText(/pick your agentic cli/i),
      'claude',
    );
    expect(gen).not.toBeDisabled();
    // The manual absolute-path entry is gone — the copy-paste prompt replaces it.
    expect(screen.queryByLabelText(/binary path/i)).not.toBeInTheDocument();
  });

  test('pick built-in → Generate mints a purpose=binary scoped token and shows the register-binary copy-paste prompt (no /connect)', async () => {
    const user = userEvent.setup();
    // Keep the kind unregistered so the poll does NOT auto-connect and the
    // prompt stays on screen for inspection.
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [{ tool: 'claude', present: false, path: null, hint: 'Register Claude Code' }],
    });
    const mintSpy = vi
      .spyOn(settingsApi, 'mintRuntimeRegistrationToken')
      .mockResolvedValue({ token: 'hr_tok_BIN123', expires_at: Date.now() / 1000 + 600 });
    renderPage();

    await user.selectOptions(
      await screen.findByLabelText(/pick your agentic cli/i),
      'claude',
    );
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    // Kind-scoped, binary-purpose token — the discriminator the backend fence
    // enforces (a binary token cannot self-register a profile via /register).
    await waitFor(() =>
      expect(mintSpy).toHaveBeenCalledWith({ name: 'claude', purpose: 'binary' }),
    );

    // The SAME prompt block the custom flow uses, pointed at register-binary:
    // carries the scoped token, targets register-binary (NOT the profile
    // register route), keeps the conformance challenge, and has no /connect URL.
    const pre = await screen.findByText(/connecting the built-in/i);
    expect(pre).toHaveTextContent('hr_tok_BIN123');
    expect(pre).toHaveTextContent('/executors/runtime/register-binary');
    expect(pre).toHaveTextContent('/executors/runtime/conformance-checkin');
    expect(pre).not.toHaveTextContent('/connect/');
    // Kind is carried by the token, never sent in the request body.
    expect(pre).not.toHaveTextContent('"kind"');
  });

  test('built-in poll flips to connected when the kind registers (present:true), via builtin path', async () => {
    const user = userEvent.setup();
    vi.spyOn(settingsApi, 'mintRuntimeRegistrationToken').mockResolvedValue({
      token: 'hr_tok_BIN',
      expires_at: Date.now() / 1000 + 600,
    });
    // The machine-local registry now reports claude registered with a path.
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [{ tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' }],
    });
    renderPage();

    await user.selectOptions(
      await screen.findByLabelText(/pick your agentic cli/i),
      'claude',
    );
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    // The SAME poll the custom flow uses (p.tool === name && p.present) flips
    // to the SAME connected card.
    expect(
      await screen.findByRole('heading', { name: /claude connected/i }),
    ).toBeInTheDocument();
    // The registered PATH is shown (real, register-sourced — honesty fence).
    expect(screen.getByText('/usr/bin/claude')).toBeInTheDocument();
    // Built-in connected copy (via:'builtin').
    expect(
      screen.getByText(/its binary path is registered on this machine/i),
    ).toBeInTheDocument();

    // Continue advances to Step 2 (org create).
    await user.click(screen.getByRole('button', { name: /continue/i }));
    expect(
      await screen.findByRole('heading', { name: /welcome to happyranch/i }),
    ).toBeInTheDocument();
  });

  test('built-in does NOT connect while present:false — the poll is registration-gated (no false-positive)', async () => {
    const user = userEvent.setup();
    vi.spyOn(settingsApi, 'mintRuntimeRegistrationToken').mockResolvedValue({
      token: 'hr_tok_BIN',
      expires_at: Date.now() / 1000 + 600,
    });
    // claude is enumerated but NOT registered (present:false) — being on PATH is
    // not sufficient in #420, so this must NOT be read as connected.
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [{ tool: 'claude', present: false, path: null, hint: 'Register Claude Code' }],
    });
    renderPage();

    await user.selectOptions(
      await screen.findByLabelText(/pick your agentic cli/i),
      'claude',
    );
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    // Stays in the waiting state; never flips to connected.
    expect(await screen.findByText(/waiting for/i)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(
      screen.queryByRole('heading', { name: /claude connected/i }),
    ).not.toBeInTheDocument();
  });

  test('Skip advances straight to Step 2', async () => {
    const user = userEvent.setup();
    renderPage();
    await skipConnect(user);
    expect(
      await screen.findByRole('heading', { name: /welcome to happyranch/i }),
    ).toBeInTheDocument();
  });

  test('a returning user (org already exists) starts at Step 2', async () => {
    vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({
      orgs: [{ slug: 'existing-org' }] as never,
      broken: [],
    });
    renderPage();
    // Lands on Step 2 (add-another copy), never showing Step 1.
    expect(
      await screen.findByRole('heading', { name: /create another org/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: /connect your agentic cli/i }),
    ).not.toBeInTheDocument();
  });
});

describe('OnboardingPage — Step 1 (custom CLI flow, preserved)', () => {
  test('Generate is disabled until the name is valid and rejects built-ins', async () => {
    const user = userEvent.setup();
    renderPage();
    await goCustom(user);
    const input = await screen.findByLabelText(/name this cli/i);
    const gen = screen.getByRole('button', { name: /generate connect prompt/i });
    expect(gen).toBeDisabled();

    // A built-in name is refused (would 422 on register / false-positive detect).
    await user.type(input, 'claude');
    expect(gen).toBeDisabled();
    expect(screen.getByText(/isn.t a built-in/i)).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, 'my-cli');
    expect(gen).not.toBeDisabled();
  });

  test('Generate mints a runtime token and shows the copy-paste prompt', async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    const mintSpy = vi
      .spyOn(settingsApi, 'mintRuntimeRegistrationToken')
      .mockResolvedValue({ token: 'hr_tok_ABC123', expires_at: Date.now() / 1000 + 600 });
    renderPage();
    await goCustom(user);

    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    await waitFor(() => expect(mintSpy).toHaveBeenCalledWith({ name: 'my-cli' }));

    // The prompt block carries the minted token and the EXISTING loopback
    // register route — no `/connect` one-click URL.
    const pre = await screen.findByText(/You're being connected to HappyRanch/i);
    expect(pre).toHaveTextContent('hr_tok_ABC123');
    expect(pre).toHaveTextContent('/executors/runtime/register');
    expect(pre).toHaveTextContent('/executors/runtime/conformance-checkin');
    expect(pre).not.toHaveTextContent('/connect/');

    // Copy writes the prompt to the clipboard.
    await user.click(screen.getByRole('button', { name: /^copy prompt$/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('hr_tok_ABC123'));
  });

  test('poll flips detecting → connected when the name appears in prereqs, then Continue advances', async () => {
    const user = userEvent.setup();
    vi.spyOn(settingsApi, 'mintRuntimeRegistrationToken').mockResolvedValue({
      token: 'hr_tok_XYZ',
      expires_at: Date.now() / 1000 + 600,
    });
    // prereqs now includes the freshly-registered runtime name.
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'my-cli', present: true, path: '/opt/bin/my-cli', hint: '' },
      ],
    });
    renderPage();
    await goCustom(user);

    await user.type(await screen.findByLabelText(/name this cli/i), 'my-cli');
    await user.click(screen.getByRole('button', { name: /generate connect prompt/i }));

    // Detect strip → connected card.
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: /my-cli connected/i }),
      ).toBeInTheDocument(),
    );
    // Resolved path from prereqs is shown (real, not fabricated).
    expect(screen.getByText('/opt/bin/my-cli')).toBeInTheDocument();

    // Continue advances to Step 2 (org create).
    await user.click(screen.getByRole('button', { name: /continue/i }));
    expect(
      await screen.findByRole('heading', { name: /welcome to happyranch/i }),
    ).toBeInTheDocument();
  });
});

describe('OnboardingPage — Step 2 (create org)', () => {
  test('welcome step advances to the create form', async () => {
    const user = userEvent.setup();
    renderPage();
    await skipConnect(user);

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
    await skipConnect(user);
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
    await skipConnect(user);
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
    await skipConnect(user);
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
    await skipConnect(user);
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

  test('a bare 409 with no recognized code renders the already-exists message', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('conflict'), { status: 409 }),
    );
    renderPage();
    await skipConnect(user);
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await user.type(screen.getByLabelText(/slug/i), 'collision');
    await user.click(screen.getByRole('button', { name: /^create org$/i }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i),
    );
  });

  test('surfaces 409 no_active_runtime with its own message, NOT already exists', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('no runtime'), { status: 409, code: 'no_active_runtime' }),
    );
    vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({ orgs: [], broken: [] });
    renderPage();
    await skipConnect(user);
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await user.type(screen.getByLabelText(/slug/i), 'my-org');
    await user.click(screen.getByRole('button', { name: /^create org$/i }));

    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toBeInTheDocument();
      // Must NOT say "already exists"
      expect(alert.textContent).not.toMatch(/already exists/i);
      // Must mention runtime / starting up / not ready
      expect(alert.textContent).toMatch(/runtime|start|ready|moment/i);
    });
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
    await skipConnect(user);

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
    await skipConnect(user);
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    expect(
      await screen.findByText(/checking host tools/i),
    ).toBeInTheDocument();
  });

  test('executor prereqs — all registered shows the X-of-Y summary and resolved paths', async () => {
    const user = userEvent.setup();
    renderPage();
    await skipConnect(user);
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await waitFor(() =>
      expect(screen.getByLabelText('Executor readiness')).toBeInTheDocument(),
    );
    // FE-computed summary from the real array.
    expect(screen.getByText(/4 of 4/)).toBeInTheDocument();
    expect(screen.getByText(/tools registered/)).toBeInTheDocument();
    // Registered path is rendered.
    expect(screen.getByText('/usr/bin/claude')).toBeInTheDocument();
  });

  test('executor prereqs — not-registered executors show the hint and a not-registered pill', async () => {
    const user = userEvent.setup();
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: 'Register Claude Code' },
        { tool: 'pi', present: false, path: null, hint: 'Register Pi' },
      ],
    });
    renderPage();
    await skipConnect(user);
    await user.click(screen.getByRole('button', { name: /create your first org/i }));

    await waitFor(() =>
      expect(screen.getByLabelText('Executor readiness')).toBeInTheDocument(),
    );
    // Summary reflects the partial count.
    expect(screen.getByText(/1 of 2/)).toBeInTheDocument();
    // Absence surfaced with the hint + a not-registered pill.
    expect(screen.getByText(/Register Pi/)).toBeInTheDocument();
    expect(screen.getByText('not registered')).toBeInTheDocument();
    // The registered executor still shows, with its pill.
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(screen.getByText('registered')).toBeInTheDocument();
  });

  test('executor prereqs — query error is silent (no panel rendered)', async () => {
    const user = userEvent.setup();
    vi.spyOn(healthApi, 'getPrereqs').mockRejectedValue(new Error('network'));
    renderPage();
    await skipConnect(user);
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
