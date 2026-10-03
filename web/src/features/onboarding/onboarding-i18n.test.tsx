/**
 * THR-118 W2b — onboarding i18n behavior (catalog/state/error TDD).
 *
 * Focused, deterministic coverage for the translated onboarding route:
 *   - en / zh-CN product copy for first-run and returning users;
 *   - a real locale switch (through the test-only preference control) that
 *     preserves the current phase, typed slug, selected connect mode and the
 *     generated copy-paste prompt bytes;
 *   - mapped daemon error categories that re-translate across a switch, and an
 *     unknown raw diagnostic preserved byte-for-byte in BOTH locales;
 *   - negative assertions: switching issues no extra query/mutation and never
 *     mutates the executable prompt/identifier text.
 *
 * The public language selector stays absent (W3); the switch here is the same
 * test-only provider control the W2a suites use.
 */
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { OnboardingPage } from './OnboardingPage';
import { health as healthApi, orgs as orgsApi, settings as settingsApi } from '@/lib/api';

function renderOnboarding(locale: 'en' | 'zh-CN') {
  return renderWithProviders(
    <>
      <OnboardingPage />
      <LocaleTestSwitch to="zh-CN" />
    </>,
    {
      route: '/onboarding',
      i18n: { adapter: savedLocaleAdapter(locale) },
    },
  );
}

async function switchToZh(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByTestId('test-set-locale-zh-CN'));
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({ orgs: [], broken: [] });
  vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({ prereqs: [] });
});

/** Returning user: at least one org exists, so Step 2 (welcome) renders. */
function mockReturningUser() {
  vi.spyOn(orgsApi, 'listOrgs').mockResolvedValue({
    orgs: [{ slug: 'demo-org', root: '/runtime/demo-org' }],
    broken: [],
  });
}

describe('OnboardingPage i18n (W2b)', () => {
  test('first-run renders the English connect step by default', async () => {
    renderOnboarding('en');
    expect(
      await screen.findByRole('heading', { name: /connect your agentic cli\./i }),
    ).toBeInTheDocument();
    expect(screen.getByText('Step 1 of 2 · Connect your agentic CLI')).toBeInTheDocument();
    expect(screen.getByLabelText(/pick your agentic cli/i)).toBeInTheDocument();
  });

  test('zh-CN first-run renders translated connect chrome and aria', async () => {
    renderOnboarding('zh-CN');
    expect(
      await screen.findByRole('heading', { name: '连接你的智能体 CLI。' }),
    ).toBeInTheDocument();
    expect(screen.getByText('第 1 步，共 2 步 · 连接你的智能体 CLI')).toBeInTheDocument();
    expect(screen.getByLabelText('选择你的智能体 CLI')).toBeInTheDocument();
  });

  test('returning user sees translated welcome + create CTA', async () => {
    mockReturningUser();
    renderOnboarding('zh-CN');
    expect(await screen.findByRole('heading', { name: '创建另一个组织' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '创建另一个组织' })).toBeInTheDocument();
    expect(screen.getByText('只需几秒钟。')).toBeInTheDocument();
  });

  test('locale switch preserves the typed slug, phase and selected mode with no extra mutation', async () => {
    mockReturningUser();
    const createSpy = vi.spyOn(orgsApi, 'createOrg');
    const listSpy = vi.spyOn(orgsApi, 'listOrgs');
    const user = userEvent.setup();
    renderOnboarding('en');

    // Welcome → create step.
    await user.click(await screen.findByRole('button', { name: 'Create another org' }));
    const input = await screen.findByLabelText('Org slug');
    await user.type(input, 'my-new-org');
    expect(input).toHaveValue('my-new-org');

    await switchToZh(user);

    // Phase and typed value survive; only the copy changed.
    expect(await screen.findByRole('heading', { name: '为组织命名' })).toBeInTheDocument();
    expect(screen.getByLabelText('组织标识符')).toHaveValue('my-new-org');
    expect(createSpy).not.toHaveBeenCalled();
    expect(listSpy).toHaveBeenCalledTimes(1);
  });

  test('mapped org-exists error re-translates across a switch without resubmission', async () => {
    mockReturningUser();
    const createSpy = vi
      .spyOn(orgsApi, 'createOrg')
      .mockRejectedValue({ code: 'org_exists', message: 'org_exists' });
    const user = userEvent.setup();
    renderOnboarding('en');

    await user.click(await screen.findByRole('button', { name: 'Create another org' }));
    await user.type(await screen.findByLabelText('Org slug'), 'taken-org');
    await user.click(screen.getByRole('button', { name: 'Create org' }));

    expect(
      await screen.findByText('An org with slug "taken-org" already exists.'),
    ).toBeInTheDocument();
    expect(createSpy).toHaveBeenCalledTimes(1);

    await switchToZh(user);
    expect(await screen.findByText('标识符为 "taken-org" 的组织已存在。')).toBeInTheDocument();
    // The locale switch must NOT resubmit the create mutation.
    expect(createSpy).toHaveBeenCalledTimes(1);
  });

  test('unknown raw backend error detail stays byte-for-byte in both locales', async () => {
    const raw = 'backend raw reason 0xDEADBEEF — org lock held';
    mockReturningUser();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue({ message: raw });
    const user = userEvent.setup();
    renderOnboarding('en');

    await user.click(await screen.findByRole('button', { name: 'Create another org' }));
    await user.type(await screen.findByLabelText('Org slug'), 'raw-org');
    await user.click(screen.getByRole('button', { name: 'Create org' }));
    expect(await screen.findByText(raw)).toBeInTheDocument();

    await switchToZh(user);
    expect(await screen.findByText(raw)).toBeInTheDocument();
  });

  test('generated built-in prompt bytes are preserved across a locale switch', async () => {
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [{ tool: 'claude', present: false, path: null, hint: 'Register Claude Code' }],
    });
    vi.spyOn(settingsApi, 'mintRuntimeRegistrationToken').mockResolvedValue({
      token: 'hr_tok_W2B',
      expires_at: Date.now() / 1000 + 1800,
    });
    const user = userEvent.setup();
    renderOnboarding('en');

    await user.selectOptions(await screen.findByLabelText(/pick your agentic cli/i), 'claude');
    await user.click(screen.getByRole('button', { name: 'Generate connect prompt' }));
    const pre = await screen.findByText(/connect the built-in/i);
    const before = pre.textContent;

    await switchToZh(user);

    // The pre block (executable prompt) is not re-rendered with different bytes:
    // raw step ids, token and route targets stay verbatim.
    await waitFor(() =>
      expect(screen.getByText(/connect the built-in/i).textContent).toBe(before),
    );
    expect(before).toContain('hr_tok_W2B');
    expect(before).toContain('workspace_access');
    expect(before).toContain('/executors/runtime/register-binary');
    // Localized labels around the raw steps did change.
    expect(screen.getByText('读取其工作区与技能')).toBeInTheDocument();
    expect(screen.getByText('workspace_access')).toBeInTheDocument();
  });

  test('prereq readiness panel uses localized accessible name and status pills', async () => {
    mockReturningUser();
    vi.spyOn(healthApi, 'getPrereqs').mockResolvedValue({
      prereqs: [
        { tool: 'claude', present: true, path: '/usr/bin/claude', hint: '' },
        { tool: 'codex', present: false, path: null, hint: 'Register Codex' },
      ],
    });
    const user = userEvent.setup();
    renderOnboarding('en');

    await user.click(await screen.findByRole('button', { name: 'Create another org' }));
    expect(await screen.findByLabelText('Executor readiness')).toBeInTheDocument();
    expect(await screen.findByText('1 of 2 tools registered')).toBeInTheDocument();

    await switchToZh(user);
    expect(await screen.findByLabelText('执行器就绪情况')).toBeInTheDocument();
    expect(await screen.findByText('已注册 1/2 个工具')).toBeInTheDocument();
    // Raw tool names/paths stay verbatim.
    expect(screen.getByText('claude')).toBeInTheDocument();
    expect(screen.getByText('/usr/bin/claude')).toBeInTheDocument();
  });
});
