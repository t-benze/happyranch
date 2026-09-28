/**
 * THR-118 W2c — Settings ▸ Executors i18n (ExecutorsSection +
 * ExecutorBinariesSection + CustomProfilesSection).
 *
 * zh-CN renders the product-owned chrome/labels/states in Chinese while
 * identifiers (executor kinds, profile names, binary paths, commands) and raw
 * daemon diagnostics stay byte-for-byte; a live en→zh-CN→en switch keeps the
 * same input node, its typed value and focus.
 */
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { ExecutorsSection } from './ExecutorsSection';

const PROFILE = {
  name: 'my-runner',
  adapter: 'claude',
  workspace_adapter_id: 'claude',
  command_adapter_id: null,
  present: true,
  path: '/usr/local/bin/my-runner-cli',
};

function stub({
  entries = [{ kind: 'claude', path: '/usr/local/bin/claude', valid: true }],
  profiles = [PROFILE],
}: {
  entries?: { kind: string; path: string | null; valid: boolean }[];
  profiles?: (typeof PROFILE)[];
} = {}) {
  server.use(
    http.get('/api/v1/executor-binaries', () => HttpResponse.json({ entries })),
    http.get('/api/v1/executors/runtime/profiles', () => HttpResponse.json({ profiles })),
    http.get('/api/v1/runtime/adapters', () => HttpResponse.json([])),
  );
}

function renderSection(locale: 'en' | 'zh-CN', withSwitches = false) {
  sessionStorage.setItem('happyranch.token', 'tok');
  return renderWithProviders(
    <>
      <ExecutorsSection />
      {withSwitches && (
        <>
          <LocaleTestSwitch to="zh-CN" />
          <LocaleTestSwitch to="en" />
        </>
      )}
    </>,
    { i18n: { adapter: savedLocaleAdapter(locale) } },
  );
}

describe('ExecutorsSection i18n (W2c)', () => {
  test('zh-CN renders localized chrome; identifiers stay verbatim; English absent', async () => {
    stub();
    renderSection('zh-CN');

    const row = await screen.findByTestId('binary-row-claude');
    // Identifiers verbatim.
    expect(within(row).getByText('claude')).toBeInTheDocument();
    expect(within(row).getByText('/usr/local/bin/claude')).toBeInTheDocument();
    expect(within(row).getByText(/^已注册路径：/)).toBeInTheDocument();
    expect(within(row).getByTestId('binary-validity')).toHaveTextContent('有效');
    expect(within(row).getByText('高级：手动输入路径')).toBeInTheDocument();
    expect(within(row).getByLabelText('更新二进制路径')).toHaveAttribute(
      'placeholder',
      '/absolute/path/to/claude',
    );
    expect(within(row).getByRole('button', { name: '验证' })).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: '注册' })).toBeInTheDocument();

    // Unregistered kinds.
    const codex = screen.getByTestId('binary-row-codex');
    expect(within(codex).getByTestId('binary-validity')).toHaveTextContent('未注册');
    expect(codex).toHaveTextContent('尚未注册路径——连接之前，守护进程无法启动 codex 智能体。');

    // Custom profiles.
    expect(await screen.findByRole('heading', { name: '自定义 CLI' })).toBeInTheDocument();
    const profile = await screen.findByTestId('profile-row-my-runner');
    expect(within(profile).getByText('my-runner')).toBeInTheDocument();
    expect(within(profile).getByText('/usr/local/bin/my-runner-cli')).toBeInTheDocument();
    expect(within(profile).getByTestId('profile-health')).toHaveTextContent('在本机上');
    expect(within(profile).getByText('此配置未记录可执行文件。')).toBeInTheDocument();
    expect(within(profile).getByRole('button', { name: '移除' })).toBeInTheDocument();

    // Section chrome.
    expect(screen.getByTestId('connect-a-cli')).toHaveTextContent('连接 CLI');
    expect(screen.getByRole('heading', { name: '按智能体分配执行器' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '智能体页面' })).toHaveAttribute('href', '../agents');

    // English copy of those keys is absent.
    for (const english of [
      'Custom CLIs',
      'Connect a CLI',
      'Per-agent executor assignment',
      'Agents page',
      'Advanced: enter path manually',
      'Remove',
      'on this machine',
      'not registered',
    ]) {
      expect(screen.queryByText(english)).not.toBeInTheDocument();
    }
    expect(screen.queryByText(/Registered path:/)).not.toBeInTheDocument();
  });

  test('zh-CN fresh-env banner and empty custom list are localized; command stays verbatim', async () => {
    stub({ entries: [], profiles: [] });
    renderSection('zh-CN');

    const banner = await screen.findByTestId('fresh-env-blocked');
    expect(
      within(banner).getByRole('heading', { name: '本机尚未注册任何执行器 CLI' }),
    ).toBeInTheDocument();
    expect(within(banner).getByText('which claude')).toBeInTheDocument();
    expect(await screen.findByTestId('custom-profiles-empty')).toHaveTextContent(
      '尚未注册自定义 CLI——请使用下方的连接 CLI连接一个。',
    );
  });

  test('zh-CN loading and error states are localized', async () => {
    sessionStorage.setItem('happyranch.token', 'tok');
    server.use(
      http.get('/api/v1/executor-binaries', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
      http.get('/api/v1/executors/runtime/profiles', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
      http.get('/api/v1/runtime/adapters', () => HttpResponse.json([])),
    );
    renderSection('zh-CN');

    expect(screen.getByText('正在加载注册表…')).toBeInTheDocument();
    expect(screen.getByText('正在加载自定义 CLI…')).toBeInTheDocument();
    expect(await screen.findByText(/^无法加载执行器二进制注册表。/)).toBeInTheDocument();
    expect(await screen.findByText(/^无法加载自定义执行器配置。/)).toBeInTheDocument();
    expect(screen.queryByText(/Could not load/)).not.toBeInTheDocument();
  });

  test('raw daemon validation diagnostic is preserved byte-for-byte in zh-CN', async () => {
    const raw = 'Path does not exist: /nope';
    stub({ entries: [] });
    server.use(
      http.post('/api/v1/executor-binaries/validate', () =>
        HttpResponse.json({ path: '/nope', valid: false, error: raw }),
      ),
    );
    const user = userEvent.setup();
    renderSection('zh-CN');

    const row = await screen.findByTestId('binary-row-claude');
    await user.click(within(row).getByText('高级：手动输入路径'));
    await user.type(within(row).getByLabelText('注册二进制路径'), '/nope');
    await user.click(within(row).getByRole('button', { name: '验证' }));
    expect(await within(row).findByTestId('binary-check-claude')).toHaveTextContent(raw);
  });

  test('en→zh-CN→en switch keeps the same input node, value and focus', async () => {
    stub();
    const user = userEvent.setup();
    renderSection('en', true);

    const row = await screen.findByTestId('binary-row-claude');
    await user.click(within(row).getByText('Advanced: enter path manually'));
    const input = within(row).getByLabelText('Update binary path');
    await user.type(input, '/opt/claude');
    expect(input).toHaveFocus();

    // A programmatic click (not user.click) so the test switch does not steal
    // focus — the assertion is that the locale change itself keeps focus.
    act(() => screen.getByTestId('test-set-locale-zh-CN').click());
    const zhInput = await within(row).findByLabelText('更新二进制路径');
    expect(zhInput).toBe(input);
    expect(zhInput).toHaveValue('/opt/claude');
    expect(zhInput).toHaveFocus();

    act(() => screen.getByTestId('test-set-locale-en').click());
    const enInput = await within(row).findByLabelText('Update binary path');
    expect(enInput).toBe(input);
    expect(enInput).toHaveValue('/opt/claude');
    expect(enInput).toHaveFocus();
  });
});
