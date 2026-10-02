/**
 * THR-118 W4b — Todos route family (list + detail + Pause/Cancel/Edit dialogs) i18n.
 *
 * Renders the real routed pages under the real I18nProvider and asserts:
 *  - zh-CN product copy for the list (populated / empty / error), the detail
 *    page, the confirm dialogs and the Edit timing dialog;
 *  - recurrence/schedule presentation goes through catalog templates while
 *    daemon values (agent names, schedule ids, briefs, instructions, IANA
 *    timezone ids, team) stay byte-identical;
 *  - a locale switch keeps the same row/dialog/input nodes, the typed draft
 *    and focus, and issues zero requests;
 *  - the Edit save diagnostic boundary: a recognized daemon code maps to
 *    catalog copy (re-translated in place), an unknown code and a code-less
 *    string detail stay verbatim, and an empty code falls back to the
 *    localized failure copy; local validation errors re-translate too.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, test } from 'vitest'
import { AppRoutes } from '@/routes'
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render'
import { server } from '@/test/server'
import type { ScheduleRecord } from '@/lib/api/types'
import { classifyTodoError, renderTodoError } from './strings'
import { translate } from '@/lib/i18n'
import { en, zhCN } from '@/lib/i18n/catalog'

const SLUG = 'happyranch'
const API = '/api/v1'

const WEEKLY: ScheduleRecord = {
  schedule_id: 'SCHEDULE-042',
  agent_name: 'investment_advisor',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-07-25T01:00:00Z',
  recurrence: { day: 'Sat', time: '09:00' },
  timezone: 'Asia/Shanghai',
  normalized_brief: 'Send the weekly market update',
  source_instruction: 'Every Saturday, send me the weekly market update.',
  status: 'armed',
  active: 1,
  expires_at: '2026-10-23T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: null,
  fire_count: 3,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
}

const MONTHLY: ScheduleRecord = {
  schedule_id: 'SCHEDULE-120',
  agent_name: 'portfolio_agent',
  team: 'engineering',
  kind: 'recurring',
  fire_at: '2026-08-10T01:00:00Z',
  recurrence: {
    freq: 'MONTHLY', interval: 2, ordinal: 'second', byday: ['MO'],
    time: '09:00', tz: 'Asia/Shanghai', until: null, count: 6, anchor_date: '2026-08-10',
  },
  timezone: 'Asia/Shanghai',
  normalized_brief: 'Review the recurring portfolio allocation',
  source_instruction: 'Review the portfolio on the second Monday every other month.',
  status: 'armed', active: 1, expires_at: null, indefinite: 0,
  spawned_task_ids: [], last_fired_at: null, fire_count: 0,
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
}

const FAILED: ScheduleRecord = {
  ...WEEKLY,
  schedule_id: 'SCHEDULE-071',
  agent_name: 'dev_agent',
  normalized_brief: 'Sync the customer changelog',
  status: 'failed',
  recurrence: { day: 'Fri', time: '17:00' },
  timezone: 'America/Chicago',
  fire_count: 6,
}

function stub(mode: { list?: ScheduleRecord[]; listError?: boolean; detail?: ScheduleRecord } = {}) {
  const detail = mode.detail ?? WEEKLY
  server.use(
    http.get(`${API}/auth/bootstrap`, () => HttpResponse.json({ token: 'tok' })),
    http.get(`${API}/orgs`, () => HttpResponse.json({ orgs: [{ slug: SLUG, root: true }], broken: [] })),
    http.get(`${API}/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({ org_age_days: 1 })),
    http.get(`${API}/orgs/${SLUG}/schedules`, () =>
      mode.listError
        ? HttpResponse.json({ detail: 'boom' }, { status: 500 })
        : HttpResponse.json({ schedules: mode.list ?? [WEEKLY, MONTHLY, FAILED] }),
    ),
    http.get(`${API}/orgs/${SLUG}/schedules/${detail.schedule_id}`, () => HttpResponse.json(detail)),
    http.all(`${API}/*`, () => HttpResponse.json({})),
  )
}

function stubPatch(id: string, body: Record<string, unknown>, status = 422) {
  server.use(
    http.patch(`${API}/orgs/${SLUG}/schedules/${id}`, () => HttpResponse.json(body, { status })),
  )
}

function mount(locale: 'en' | 'zh-CN', route = `/orgs/${SLUG}/todos`) {
  return renderWithProviders(
    <>
      <AppRoutes />
      <LocaleTestSwitch to="zh-CN" />
      <LocaleTestSwitch to="en" />
    </>,
    { route, i18n: { adapter: savedLocaleAdapter(locale) } },
  )
}

async function switchLocale(to: 'en' | 'zh-CN') {
  await act(async () => {
    fireEvent.click(screen.getByTestId(`test-set-locale-${to}`))
  })
}

async function countRequests(fn: () => Promise<void>): Promise<string[]> {
  const seen: string[] = []
  const listener = ({ request }: { request: Request }) => {
    seen.push(`${request.method} ${new URL(request.url).pathname}`)
  }
  server.events.on('request:start', listener)
  try {
    await fn()
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 60))
    })
  } finally {
    server.events.removeListener('request:start', listener)
  }
  return seen
}

async function openEdit(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole('button', { name: /^(Edit|编辑)$/ }))
  const heading = await screen.findByRole('heading', { name: /^(Edit timing|编辑时间)$/ })
  const dialog = heading.closest('[role="dialog"]')
  if (!(dialog instanceof HTMLElement)) throw new Error('edit heading is not inside a dialog')
  return dialog
}

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok')
  localStorage.clear()
})

describe('Todos list i18n', () => {
  test('populated list: zh-CN chrome + recurrence templates, verbatim daemon values, same row node and zero requests', async () => {
    stub()
    mount('zh-CN')

    expect(await screen.findByText('Send the weekly market update')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '待办' })).toBeInTheDocument()
    expect(screen.getByText('智能体承诺')).toBeInTheDocument()
    expect(screen.getByText('智能体根据你的指示创建的计划承诺。')).toBeInTheDocument()
    for (const tab of ['全部', '进行中', '已暂停', '历史']) {
      expect(screen.getByRole('button', { name: tab })).toBeInTheDocument()
    }
    expect(screen.getByRole('combobox', { name: '按智能体筛选' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '全部智能体' })).toBeInTheDocument()
    expect(screen.getByText('2 个进行中 · 1 个需要关注')).toBeInTheDocument()
    expect(screen.getAllByText('已就绪').length).toBe(2)
    expect(screen.getAllByText('下次触发').length).toBe(2)
    expect(screen.getByText('每周六 09:00')).toBeInTheDocument()
    expect(screen.getByText('曾为每周五 17:00')).toBeInTheDocument()
    expect(
      screen.getByText('每 2 个月的第二个星期一 09:00（Asia/Shanghai） · 6 次后结束'),
    ).toBeInTheDocument()
    expect(screen.getByText('3 次运行')).toBeInTheDocument()
    expect(screen.getAllByText(/^审核截止 /).length).toBeGreaterThan(0)
    // Daemon values stay byte-verbatim.
    expect(screen.getByText('SCHEDULE-042')).toBeInTheDocument()
    expect(screen.getAllByText('investment_advisor').length).toBeGreaterThan(0)
    expect(screen.getByRole('option', { name: 'portfolio_agent' })).toBeInTheDocument()

    const row = screen.getByText('Send the weekly market update').closest('a')
    const requests = await countRequests(async () => {
      await switchLocale('en')
      expect(screen.getByRole('heading', { name: 'Todos' })).toBeInTheDocument()
      expect(screen.getByText('2 active · 1 needs attention')).toBeInTheDocument()
      expect(screen.getByText('Every Sat at 09:00')).toBeInTheDocument()
      expect(
        screen.getByText(
          'Every 2 months on the second Monday at 09:00 Asia/Shanghai · Ends after 6 occurrences',
        ),
      ).toBeInTheDocument()
      expect(screen.getByText('3 runs')).toBeInTheDocument()
      expect(screen.getByText('Send the weekly market update').closest('a')).toBe(row)
      await switchLocale('zh-CN')
      expect(screen.getByText('Send the weekly market update').closest('a')).toBe(row)
      expect(screen.getByText('每周六 09:00')).toBeInTheDocument()
    })
    expect(requests).toEqual([])
  })

  test('empty and error states are localized', async () => {
    stub({ list: [] })
    const { unmount } = mount('zh-CN')
    expect(await screen.findByText('暂无待办')).toBeInTheDocument()
    unmount()
    stub({ listError: true })
    mount('zh-CN')
    expect(await screen.findByText('无法加载待办')).toBeInTheDocument()
    expect(screen.getByText('服务器返回了错误。你可以重试。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })
})

describe('Todo detail i18n', () => {
  test('zh-CN detail copy, verbatim values and the confirm dialogs', async () => {
    stub()
    mount('zh-CN', `/orgs/${SLUG}/todos/SCHEDULE-042`)
    expect(await screen.findByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument()
    expect(screen.getByText('计划')).toBeInTheDocument()
    expect(screen.getByText(/^每周六 09:00 · 审核截止 /)).toBeInTheDocument()
    expect(screen.getAllByText('每周').length).toBe(2)
    expect(screen.getByText('规范化承诺')).toBeInTheDocument()
    expect(screen.getByText('原始指示')).toBeInTheDocument()
    expect(screen.getByText('查看相关活动')).toBeInTheDocument()
    expect(screen.getByText('记录详情')).toBeInTheDocument()
    expect(screen.getByText(/^审核截止 .*除非此待办创建时设为无限期/)).toBeInTheDocument()
    // Verbatim daemon values.
    expect(screen.getByText('Every Saturday, send me the weekly market update.')).toBeInTheDocument()
    expect(screen.getAllByText('Asia/Shanghai').length).toBeGreaterThan(0)
    expect(screen.getByText('engineering')).toBeInTheDocument()
    expect(screen.getByText('task_id=SCHEDULE-042')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    const dialog = (await screen.findByText('取消此待办')).closest('[role="dialog"]') as HTMLElement
    expect(within(dialog).getByText('取消后，此待办无法重新激活。智能体将不会再从中收到任何任务。')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: '取消待办' })
    confirm.focus()
    const requests = await countRequests(async () => {
      await switchLocale('en')
      expect(within(dialog).getByText('Cancel this Todo')).toBeInTheDocument()
      expect(within(dialog).getByRole('button', { name: 'Cancel Todo' })).toBe(confirm)
      expect(document.activeElement).toBe(confirm)
    })
    expect(requests).toEqual([])
  })

  test('Edit dialog keeps the typed draft, the same nodes and focus across en -> zh-CN -> en with zero requests', async () => {
    stub()
    mount('en', `/orgs/${SLUG}/todos/SCHEDULE-042`)
    const dialog = await openEdit()
    const time = within(dialog).getByLabelText('Time') as HTMLInputElement
    fireEvent.change(time, { target: { value: '10:30' } })
    time.focus()
    expect(within(dialog).getByText('Expected next fire · Asia/Shanghai')).toBeInTheDocument()

    const requests = await countRequests(async () => {
      await switchLocale('zh-CN')
      expect(within(dialog).getByRole('heading', { name: '编辑时间' })).toBeInTheDocument()
      expect(within(dialog).getByRole('button', { name: '关闭' })).toBeInTheDocument()
      expect(within(dialog).getByText('预计下次触发 · Asia/Shanghai')).toBeInTheDocument()
      expect(within(dialog).getByRole('button', { name: '保存更改' })).toBeInTheDocument()
      const zhTime = within(dialog).getByLabelText('时间') as HTMLInputElement
      expect(zhTime).toBe(time)
      expect(zhTime.value).toBe('10:30')
      expect(document.activeElement).toBe(time)
      // Daemon values inside the dialog stay verbatim.
      expect(within(dialog).getByText('Send the weekly market update')).toBeInTheDocument()
      await switchLocale('en')
      expect(within(dialog).getByRole('heading', { name: 'Edit timing' })).toBeInTheDocument()
      expect(within(dialog).getByLabelText('Time')).toBe(time)
      expect(time.value).toBe('10:30')
      expect(document.activeElement).toBe(time)
    })
    expect(requests).toEqual([])
  })

  test('recurring Edit fields are localized and a local validation error re-translates in place', async () => {
    stub({ detail: MONTHLY })
    mount('zh-CN', `/orgs/${SLUG}/todos/SCHEDULE-120`)
    expect(
      await screen.findByText('每 2 个月的第二个星期一 09:00（Asia/Shanghai） · 6 次后结束'),
    ).toBeInTheDocument()
    const dialog = await openEdit()
    expect(within(dialog).getByText('每月模式')).toBeInTheDocument()
    expect(within(dialog).getByText('指定星期')).toBeInTheDocument()
    expect(within(dialog).getByText('指定次数后')).toBeInTheDocument()
    expect(within(dialog).getByText('从此日期重新定相（可选）')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('重复间隔'), { target: { value: '0' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存更改' }))
    expect(await within(dialog).findByText('重复间隔必须是正整数。')).toBeInTheDocument()
    await switchLocale('en')
    expect(within(dialog).getByText('Repeat interval must be a positive whole number.')).toBeInTheDocument()
  })

  test('recognized save code maps to catalog copy and re-translates in place', async () => {
    stub()
    stubPatch('SCHEDULE-042', { detail: { code: 'invalid_fire_at', got: 'x' } })
    mount('zh-CN', `/orgs/${SLUG}/todos/SCHEDULE-042`)
    const dialog = await openEdit()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存更改' }))
    expect(await within(dialog).findByText('触发时间无效。')).toBeInTheDocument()
    await switchLocale('en')
    expect(within(dialog).getByText('The fire time is invalid.')).toBeInTheDocument()
  })

  test('unknown code and code-less string detail stay verbatim', async () => {
    stub()
    stubPatch('SCHEDULE-042', { detail: { code: 'schedule_store_locked' } })
    mount('zh-CN', `/orgs/${SLUG}/todos/SCHEDULE-042`)
    const dialog = await openEdit()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存更改' }))
    expect(await within(dialog).findByText('schedule_store_locked')).toBeInTheDocument()

    stubPatch('SCHEDULE-042', { detail: 'fire_at must be in the future' })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存更改' }))
    expect(await within(dialog).findByText('fire_at must be in the future')).toBeInTheDocument()
    expect(within(dialog).queryByText(/API 422/)).toBeNull()
  })

  test('empty code falls back to the localized failure copy', async () => {
    stub()
    stubPatch('SCHEDULE-042', { detail: { code: '' } }, 500)
    mount('zh-CN', `/orgs/${SLUG}/todos/SCHEDULE-042`)
    const dialog = await openEdit()
    fireEvent.click(within(dialog).getByRole('button', { name: '保存更改' }))
    expect(await within(dialog).findByText('编辑失败。')).toBeInTheDocument()
    await waitFor(() => expect(within(dialog).queryByText(/API 500/)).toBeNull())
    await switchLocale('en')
    expect(within(dialog).getByText('Edit failed.')).toBeInTheDocument()
  })
})

describe('classifyTodoError', () => {
  const en = (key: Parameters<typeof translate>[1]) => translate('en', key)
  test('boundary: recognized / unknown / detail / Error / thrown string / fallback', () => {
    expect(renderTodoError(classifyTodoError({ code: 'not_found' }, 'todos.error.editFailed'), en)).toBe(
      'This Todo no longer exists.',
    )
    expect(classifyTodoError({ code: 'odd_code' }, 'todos.error.editFailed')).toEqual({ kind: 'raw', text: 'odd_code' })
    expect(classifyTodoError({ code: null, detail: 'bad' }, 'todos.error.editFailed')).toEqual({ kind: 'raw', text: 'bad' })
    expect(classifyTodoError(new Error('boom'), 'todos.error.editFailed')).toEqual({ kind: 'raw', text: 'boom' })
    expect(classifyTodoError('thrown', 'todos.error.editFailed')).toEqual({ kind: 'raw', text: 'thrown' })
    expect(classifyTodoError({ code: '', detail: { code: '' } }, 'todos.error.editFailed')).toEqual({
      kind: 'message',
      key: 'todos.error.editFailed',
    })
  })
})

// THR-118 W4b fix-forward (TASK-9461 F-A): every count-bearing Todos message is
// a per-locale plural object (English one + other, Chinese other) selected by
// a numeric `count`.
const TODOS_COUNT_KEYS = [
  'todos.list.summaryActive',
  'todos.list.summaryAttention',
  'todos.row.runs',
  'todos.recurrence.everyN.daily',
  'todos.recurrence.everyN.weekly',
  'todos.recurrence.everyN.monthly',
  'todos.recurrence.everyN.yearly',
  'todos.recurrence.everyN.cycle',
  'todos.recurrence.endsAfter',
] as const

describe('Todos count plurals', () => {
  test('count-bearing catalog entries are plural objects in both locales', () => {
    for (const key of TODOS_COUNT_KEYS) {
      expect(Object.keys(en[key] as object).sort(), key).toEqual(['one', 'other'])
      expect(Object.keys(zhCN[key] as object), key).toEqual(['other'])
    }
  })

  test('singular and plural render correctly in en and zh-CN at the real call sites', async () => {
    const one = { ...WEEKLY, fire_count: 1 }
    const monthlyOnce: ScheduleRecord = {
      ...MONTHLY,
      recurrence: { ...(MONTHLY.recurrence as Record<string, unknown>), count: 1 } as ScheduleRecord['recurrence'],
    }
    const failedToo = { ...FAILED, schedule_id: 'SCHEDULE-072', normalized_brief: 'Rotate the on-call roster' }
    stub({ list: [one, monthlyOnce, FAILED, failedToo] })
    mount('en')
    expect(await screen.findByText('1 run')).toBeInTheDocument()
    expect(screen.getByText('2 active · 2 need attention')).toBeInTheDocument()
    expect(screen.getAllByText('6 runs').length).toBe(2)
    expect(
      screen.getByText('Every 2 months on the second Monday at 09:00 Asia/Shanghai · Ends after 1 occurrence'),
    ).toBeInTheDocument()
    await switchLocale('zh-CN')
    expect(screen.getByText('1 次运行')).toBeInTheDocument()
    expect(screen.getByText('2 个进行中 · 2 个需要关注')).toBeInTheDocument()
    expect(screen.getByText('每 2 个月的第二个星期一 09:00（Asia/Shanghai） · 1 次后结束')).toBeInTheDocument()
  })

  test('a single active Todo reads in the singular', async () => {
    stub({ list: [WEEKLY, FAILED] })
    mount('en')
    expect(await screen.findByText('1 active · 1 needs attention')).toBeInTheDocument()
  })
})
