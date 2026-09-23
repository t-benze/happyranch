/**
 * THR-118 W2c — Daemon / Capacity i18n.
 *
 * Product copy renders in zh-CN while every number, config key, daemon-supplied
 * guidance/provenance string and raw API error text stays byte-for-byte. A live
 * locale switch keeps the same input node, its value and its focus, and
 * re-translates an error that is already on screen.
 */
import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { useI18n } from '@/hooks/i18n';
import { ApiError } from '@/lib/api';
import type { Locale } from '@/lib/i18n';
import { savedLocaleAdapter } from '@/test/render';
import { DaemonCapacitySection } from './DaemonCapacitySection';
import { renderGuarded } from './capacityTestMount';

const hooks = vi.hoisted(() => ({ query: vi.fn(), mutation: vi.fn() }));
vi.mock('@/hooks/settings', () => ({
  useDaemonCapacity: hooks.query,
  useUpdateDaemonCapacity: hooks.mutation,
}));

const REV_A = `sha256:${'a'.repeat(64)}`;

function snapshot() {
  return {
    running_at_daemon_start: { queue_workers: 3, host_global_session_cap: 10 },
    running_provenance: 'startup-resolved settings snapshot',
    persisted_yaml: { queue_workers: 3, host_global_session_cap: 10 },
    next_start: { queue_workers: 3, host_global_session_cap: 10 },
    environment_shadowed: [] as string[],
    environment_warning: null as string | null,
    producer_envelope: 10,
    producer_components: {
      task_workers: 3, thread_workers: 4, dream_workers: 1, wake_workers: 1, schedule_workers: 1,
    },
    effective_admission_cap: 10,
    effective_admission_reason: 'startup policy',
    warnings: [] as string[],
    revision: REV_A,
    restart_required: false,
    restart_pending: false,
    guidance: {
      queue_workers: 'Starting guidance 4-6.',
      host_global_session_cap: 'Starting guidance 11-13.',
      enforced: false,
    },
    authorization: 'daemon bearer required',
  };
}

function loaded() {
  hooks.query.mockReturnValue({
    data: snapshot(),
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn().mockResolvedValue({ data: snapshot() }),
    isFetching: false,
    observation: {
      issuedSeq: 1, settledSeq: 2, outcome: 'usable',
      receiptAt: Date.parse('2026-09-21T09:08:07'), sourceRevision: REV_A,
    },
  });
  hooks.mutation.mockReturnValue({ mutateAsync: vi.fn(), isPending: false, settlementOf: () => null });
}

let setLocaleProbe: ((next: Locale) => void) | null = null;
function LocaleProbe(): null {
  setLocaleProbe = useI18n().setLocale;
  return null;
}

function mount(locale: Locale) {
  return renderGuarded(
    <>
      <DaemonCapacitySection />
      <LocaleProbe />
    </>,
    { entries: ['/orgs/alpha/settings/daemon-capacity'], i18nAdapter: savedLocaleAdapter(locale) },
  );
}

function switchTo(next: Locale) {
  act(() => setLocaleProbe?.(next));
}

beforeEach(() => {
  vi.clearAllMocks();
  setLocaleProbe = null;
  loaded();
});

describe('DaemonCapacitySection i18n (W2c capacity)', () => {
  test('zh-CN renders localized product copy and keeps every number, key and daemon string verbatim', () => {
    mount('zh-CN');
    expect(screen.getByRole('heading', { name: '容量' })).toBeInTheDocument();
    expect(screen.getByText('当前运行')).toBeInTheDocument();
    expect(screen.getByLabelText(/任务会话槽位/)).toHaveValue('3');
    expect(screen.getByLabelText(/主机会话准入上限/)).toHaveValue('10');
    expect(screen.getByLabelText('更改原因')).toHaveAttribute(
      'placeholder',
      '例如：新增第二个团队后队列延迟增加；提高任务槽位。',
    );
    expect(screen.getByRole('button', { name: '保存到下次重启' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '放弃草稿' })).toBeInTheDocument();
    expect(screen.getByText('容量详情')).toBeInTheDocument();

    // English product copy of the same keys is absent.
    expect(screen.queryByRole('heading', { name: 'Capacity' })).not.toBeInTheDocument();
    expect(screen.queryByText('Running now')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save for next restart' })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent('Guidance only, not an enforced range.');

    // Verbatim: config keys, daemon guidance, daemon provenance, revision, numbers.
    expect(screen.getAllByText('queue_workers').length).toBeGreaterThan(0);
    expect(screen.getAllByText('host_global_session_cap').length).toBeGreaterThan(0);
    expect(document.body).toHaveTextContent('Starting guidance 4-6. 仅供参考，并非强制范围。');
    expect(screen.getByText('startup-resolved settings snapshot')).toBeInTheDocument();
    expect(screen.getByText(REV_A)).toBeInTheDocument();
    expect(document.body).toHaveTextContent('3 个任务，4 个会话线程，1 个梦境，1 个唤醒，1 个计划');
    expect(document.body).toHaveTextContent('最近接收于 09:08:07（本浏览器时钟）');
  });

  test('zh-CN loading state is localized', () => {
    hooks.query.mockReturnValue({
      isLoading: true, isError: false, error: null, refetch: vi.fn(), isFetching: true, observation: null,
    });
    mount('zh-CN');
    expect(screen.getByRole('status')).toHaveTextContent('正在加载守护进程容量…');
    expect(screen.queryByText(/Loading daemon capacity/)).not.toBeInTheDocument();
  });

  test('zh-CN read error localizes product copy and preserves the raw error text byte-for-byte', () => {
    const error = new ApiError(500, 'config_parse_failed', {});
    hooks.query.mockReturnValue({
      data: undefined, isLoading: false, isError: true, error, refetch: vi.fn(), isFetching: false, observation: null,
    });
    mount('zh-CN');
    expect(screen.getByRole('alert')).toHaveTextContent(
      `无法加载守护进程容量。未显示任何值。${error.message}`,
    );
    expect(error.message).toBe('API 500 (config_parse_failed)');
    expect(screen.queryByText(/Could not load daemon capacity/)).not.toBeInTheDocument();
  });

  test('a validation error raised in zh-CN re-translates on a locale switch', async () => {
    const user = userEvent.setup();
    mount('zh-CN');
    await user.clear(screen.getByLabelText(/任务会话槽位/));
    await user.type(screen.getByLabelText('更改原因'), 'measured');
    await user.click(screen.getByRole('button', { name: '保存到下次重启' }));
    expect(await screen.findByText('任务会话槽位为必填项。')).toBeInTheDocument();
    expect(screen.getByText('保存前请检查高亮的字段。未发送任何内容。')).toBeInTheDocument();

    switchTo('en');
    expect(await screen.findByText('Task session slots is required.')).toBeInTheDocument();
    expect(
      screen.getByText('Check the highlighted fields before saving. Nothing was sent.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('任务会话槽位为必填项。')).not.toBeInTheDocument();
  });

  test('switching en -> zh-CN -> en keeps the same input node, its value and its focus', async () => {
    const user = userEvent.setup();
    mount('en');
    const input = screen.getByLabelText(/Task session slots/) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, '7');
    expect(input).toHaveFocus();

    switchTo('zh-CN');
    await waitFor(() => expect(screen.getByRole('heading', { name: '容量' })).toBeInTheDocument());
    const zhInput = screen.getByLabelText(/任务会话槽位/);
    expect(zhInput).toBe(input);
    expect(zhInput).toHaveValue('7');
    expect(zhInput).toHaveFocus();

    switchTo('en');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Capacity' })).toBeInTheDocument());
    const enInput = screen.getByLabelText(/Task session slots/);
    expect(enInput).toBe(input);
    expect(enInput).toHaveValue('7');
    expect(enInput).toHaveFocus();
  });
});
