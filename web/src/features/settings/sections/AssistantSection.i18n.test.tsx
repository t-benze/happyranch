/**
 * THR-118 W2c — Settings → Assistant i18n coverage.
 *
 * zh-CN product copy (headings, labels, aria, loading/error states, dialog
 * copy) renders from the catalog; identifiers and raw daemon diagnostics stay
 * byte-for-byte; a live en → zh-CN → en switch keeps the same input node,
 * its value and its focus.
 */
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, test } from 'vitest';
import type { AssistantStatus } from '@/lib/api/types';
import type { Locale } from '@/lib/i18n';
import { useI18n } from '@/hooks/i18n';
import { renderWithProviders, savedLocaleAdapter } from '@/test/render';
import { server } from '@/test/server';
import { AssistantSection } from './AssistantSection';

const SLUG = 'alpha';
const WORKSPACE = '/rt/system/assistant/workspace';

function stubStatus(status: AssistantStatus) {
  server.use(http.get('/api/v1/assistant/status', () => HttpResponse.json(status)));
}

let setLocaleRef: ((next: Locale) => void) | null = null;
function LocaleProbe(): null {
  setLocaleRef = useI18n().setLocale;
  return null;
}

function render(locale: Locale) {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(
    <>
      <Routes>
        <Route path="/orgs/:slug/settings/assistant" element={<AssistantSection />} />
      </Routes>
      <LocaleProbe />
    </>,
    {
      route: `/orgs/${SLUG}/settings/assistant`,
      i18n: { adapter: savedLocaleAdapter(locale) },
    },
  );
}

describe('AssistantSection i18n (W2c)', () => {
  test('zh-CN configured: translated chrome and aria, identifiers verbatim', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: WORKSPACE,
      detail: null,
    });
    render('zh-CN');

    expect(await screen.findByText('已配置')).toBeInTheDocument();
    const statusCard = screen.getByRole('region', { name: '助手状态' });
    expect(within(statusCard).getByText('状态')).toBeInTheDocument();
    expect(within(statusCard).getByText('工作区')).toBeInTheDocument();
    // Identifiers stay byte-for-byte.
    expect(within(statusCard).getByText('claude')).toBeInTheDocument();
    expect(within(statusCard).getByText(WORKSPACE)).toBeInTheDocument();

    expect(screen.getByRole('region', { name: '设置操作' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新配置…' })).toBeInTheDocument();

    const register = screen.getByRole('region', { name: '注册执行器' });
    expect(within(register).getByRole('heading', { name: '切换执行器' })).toBeInTheDocument();
    expect(within(register).getByText('注册立即生效，无需重启守护进程。')).toBeInTheDocument();
    expect(screen.getByLabelText('命令')).toBeInTheDocument();
    expect(screen.getByLabelText('Argv（可选——默认为该命令）')).toHaveAttribute(
      'placeholder',
      'claude --dangerously-skip-permissions',
    );
    expect(screen.getByRole('combobox', { name: '执行器' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '注册' })).toBeInTheDocument();

    // English copy of the same keys is absent.
    expect(screen.queryByText('Configured')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Assistant status' })).not.toBeInTheDocument();
    expect(screen.queryByText('Switch executor')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Registration applies immediately; no daemon restart is required.'),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Register' })).not.toBeInTheDocument();
  });

  test('zh-CN reconfigure dialog copy is translated', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: WORKSPACE,
      detail: null,
    });
    const user = userEvent.setup();
    render('zh-CN');

    await user.click(await screen.findByRole('button', { name: '重新配置…' }));
    const dialog = await screen.findByRole('dialog', { name: '要重新配置助手吗？' });
    expect(
      within(dialog).getByText('这会关闭所有打开的助手会话并清除已保存的配置。你需要重新注册执行器。'),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '重新配置' })).toBeInTheDocument();
  });

  test('zh-CN stale: raw daemon detail is preserved verbatim next to translated copy', async () => {
    stubStatus({
      state: 'stale_or_broken',
      selected_executor: 'codex',
      workspace_path: WORKSPACE,
      detail: 'workspace missing AGENTS.md',
    });
    render('zh-CN');

    expect(await screen.findByText('已过期或损坏')).toBeInTheDocument();
    expect(screen.getByText('workspace missing AGENTS.md')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '修复' })).toBeInTheDocument();
    expect(screen.queryByText('Stale or broken')).not.toBeInTheDocument();
  });

  test('zh-CN uninitialized: self-registration steps keep the CLI command verbatim', async () => {
    stubStatus({
      state: 'uninitialized',
      selected_executor: null,
      workspace_path: null,
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/init', () =>
        HttpResponse.json({
          state: 'uninitialized',
          selected_executor: null,
          workspace_path: WORKSPACE,
          detail: null,
        }),
      ),
    );
    const user = userEvent.setup();
    render('zh-CN');

    await user.click(await screen.findByRole('button', { name: '初始化工作区' }));
    expect(await screen.findByText('自行注册')).toBeInTheDocument();
    const code = screen.getByText('happyranch assistant register');
    expect(code.tagName).toBe('CODE');
    expect(code.closest('li')).toHaveTextContent('让它注册自己；它会运行 happyranch assistant register。');
    expect(
      within(screen.getByRole('region', { name: '注册执行器' })).getByRole('heading', {
        name: '注册执行器',
      }),
    ).toBeInTheDocument();
  });

  test('zh-CN register errors: validation copy translated, raw daemon code verbatim', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: WORKSPACE,
      detail: null,
    });
    server.use(
      http.post('/api/v1/assistant/register', () =>
        HttpResponse.json(
          { detail: { code: 'assistant_executable_not_found', executable: 'ghost-cli' } },
          { status: 400 },
        ),
      ),
    );
    const user = userEvent.setup();
    render('zh-CN');

    await user.click(await screen.findByRole('button', { name: '注册' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请输入要启动的命令。');

    await user.type(screen.getByLabelText('命令'), 'ghost-cli');
    await user.click(screen.getByRole('button', { name: '注册' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'assistant_executable_not_found: ghost-cli',
    );
  });

  test('zh-CN loading state', async () => {
    server.use(
      http.get('/api/v1/assistant/status', async () => {
        await delay('infinite');
        return HttpResponse.json({});
      }),
    );
    render('zh-CN');
    expect(await screen.findByText('正在加载…')).toBeInTheDocument();
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument();
  });

  test('zh-CN load error', async () => {
    server.use(
      http.get('/api/v1/assistant/status', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 }),
      ),
    );
    render('zh-CN');
    expect(await screen.findByText('无法加载助手状态。', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText('Could not load assistant status.')).not.toBeInTheDocument();
  });

  test('locale switch en → zh-CN → en keeps the command input node, value and focus', async () => {
    stubStatus({
      state: 'configured',
      selected_executor: 'claude',
      workspace_path: WORKSPACE,
      detail: null,
    });
    const user = userEvent.setup();
    render('en');

    const input = await screen.findByLabelText('Command');
    await user.type(input, 'my-cli --flag');
    expect(input).toHaveFocus();

    act(() => setLocaleRef!('zh-CN'));
    expect(await screen.findByLabelText('命令')).toBe(input);
    expect(input).toHaveValue('my-cli --flag');
    expect(input).toHaveFocus();

    act(() => setLocaleRef!('en'));
    expect(await screen.findByLabelText('Command')).toBe(input);
    expect(input).toHaveValue('my-cli --flag');
    expect(input).toHaveFocus();
  });
});
