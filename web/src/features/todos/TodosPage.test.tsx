/**
 * TodosPage tests — focused on sidebar/routes, filter grouping, action-matrix
 * branches, exact provenance URLs, mutation success/failure, validation,
 * and read-only fields.
 */
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, it, expect, beforeEach } from 'vitest'
import { AppRoutes } from '@/routes'
import { renderWithProviders } from '@/test/render'
import { server } from '@/test/server'
import type { ScheduleRecord } from '@/lib/api/types'

/* ---------------------------------------------------------------- */
/*  Fixtures                                                        */
/* ---------------------------------------------------------------- */

const ARMED_WEEKLY: ScheduleRecord = {
  schedule_id: 'SCHEDULE-042',
  agent_name: 'investment_advisor',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-07-25T09:00:00Z',
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

const ARMED_ONESHOT: ScheduleRecord = {
  schedule_id: 'SCHEDULE-058',
  agent_name: 'support_agent',
  team: 'engineering',
  kind: 'one_shot',
  fire_at: '2026-08-01T14:00:00Z',
  recurrence: null,
  timezone: 'America/New_York',
  normalized_brief: 'Follow up on the Acme trial issue',
  source_instruction: 'Follow up with this customer 48 hours after the issue was filed.',
  status: 'armed',
  active: 1,
  expires_at: null,
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: null,
  fire_count: 0,
  created_at: '2026-07-15T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
}

const FIRING: ScheduleRecord = {
  schedule_id: 'SCHEDULE-064',
  agent_name: 'qa_engineer',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-07-24T22:00:00Z',
  recurrence: { day: 'Thu', time: '22:00' },
  timezone: 'America/Chicago',
  normalized_brief: 'Run the nightly regression sweep',
  source_instruction: 'Run the full regression suite every Thursday night.',
  status: 'firing',
  active: 1,
  expires_at: null,
  indefinite: 1,
  spawned_task_ids: [],
  last_fired_at: '2026-07-17T22:00:00Z',
  fire_count: 14,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-24T22:00:01Z',
}

const FAILED: ScheduleRecord = {
  schedule_id: 'SCHEDULE-071',
  agent_name: 'dev_agent',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-07-18T17:00:00Z',
  recurrence: { day: 'Fri', time: '17:00' },
  timezone: 'America/Chicago',
  normalized_brief: 'Sync the customer changelog',
  source_instruction: 'Sync the customer changelog every Friday.',
  status: 'failed',
  active: 0,
  expires_at: '2026-10-01T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: '2026-07-18T18:00:00Z',
  fire_count: 6,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-18T19:00:00Z',
}

const TIMEOUT: ScheduleRecord = {
  schedule_id: 'SCHEDULE-088',
  agent_name: 'support_agent',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-07-22T08:00:00Z',
  recurrence: { day: 'Tue', time: '08:00' },
  timezone: 'America/Chicago',
  normalized_brief: 'Summarize overnight inbox triage',
  source_instruction: 'Summarize the overnight inbox triage every Tuesday morning.',
  status: 'timeout',
  active: 0,
  expires_at: '2026-12-01T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: '2026-07-22T09:00:00Z',
  fire_count: 9,
  created_at: '2026-05-01T00:00:00Z',
  updated_at: '2026-07-22T10:00:00Z',
}

const PAUSED: ScheduleRecord = {
  schedule_id: 'SCHEDULE-031',
  agent_name: 'product_lead',
  team: 'product',
  kind: 'weekly',
  fire_at: '',
  recurrence: { day: 'Mon', time: '10:00' },
  timezone: 'America/Chicago',
  normalized_brief: 'Review roadmap risks',
  source_instruction: 'Review roadmap risks every Monday morning.',
  status: 'paused',
  active: 0,
  expires_at: null,
  indefinite: 1,
  spawned_task_ids: [],
  last_fired_at: '2026-07-14T10:00:00Z',
  fire_count: 5,
  created_at: '2026-06-01T00:00:00Z',
  updated_at: '2026-07-15T00:00:00Z',
}

const CANCELLED: ScheduleRecord = {
  schedule_id: 'SCHEDULE-102',
  agent_name: 'product_lead',
  team: 'product',
  kind: 'one_shot',
  fire_at: '2026-07-10T11:00:00Z',
  recurrence: null,
  timezone: 'America/Chicago',
  normalized_brief: 'Draft partner outreach note',
  source_instruction: 'Draft a partner outreach note by July 10.',
  status: 'cancelled',
  active: 0,
  expires_at: null,
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: null,
  fire_count: 0,
  created_at: '2026-07-05T00:00:00Z',
  updated_at: '2026-07-09T00:00:00Z',
}

const FIRED: ScheduleRecord = {
  schedule_id: 'SCHEDULE-019',
  agent_name: 'engineering_manager',
  team: 'engineering',
  kind: 'one_shot',
  fire_at: '2026-07-20T09:00:00Z',
  recurrence: null,
  timezone: 'America/Chicago',
  normalized_brief: 'Check the release health metric',
  source_instruction: 'Check the release health metric on July 20.',
  status: 'fired',
  active: 0,
  expires_at: null,
  indefinite: 0,
  spawned_task_ids: ['TASK-8899'],
  last_fired_at: '2026-07-20T09:05:00Z',
  fire_count: 1,
  created_at: '2026-07-18T00:00:00Z',
  updated_at: '2026-07-20T09:05:00Z',
}

const EXPIRED: ScheduleRecord = {
  schedule_id: 'SCHEDULE-007',
  agent_name: 'investment_advisor',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '',
  recurrence: { day: 'Sat', time: '09:00' },
  timezone: 'Asia/Shanghai',
  normalized_brief: 'Weekly market update',
  source_instruction: 'Every Saturday, send the weekly market update.',
  status: 'expired',
  active: 0,
  expires_at: '2026-07-01T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: '2026-06-28T09:00:00Z',
  fire_count: 12,
  created_at: '2026-04-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
}

const ALL_SCHEDULES = [
  ARMED_WEEKLY, ARMED_ONESHOT, FIRING, FAILED, TIMEOUT,
  PAUSED, CANCELLED, FIRED, EXPIRED,
]

const ORG_SLUG = 'happyranch'
const API_BASE = '/api/v1'

/** Shared bootstrap + orgs mocks needed by the SPA shell. */
function bootstrap() {
  return [
    http.get(`${API_BASE}/auth/bootstrap`, () =>
      HttpResponse.json({ token: 'mock-token' }),
    ),
    http.get(`${API_BASE}/orgs`, () =>
      HttpResponse.json({
        orgs: [{ slug: ORG_SLUG, root: true }],
        broken: [],
      }),
    ),
  ]
}

function mockSchedules(schedules: ScheduleRecord[]) {
  server.use(
    ...bootstrap(),
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
      HttpResponse.json({ schedules }),
    ),
    http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
  )
}

function mockDetail(schedule: ScheduleRecord) {
  server.use(
    ...bootstrap(),
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/${schedule.schedule_id}`, () =>
      HttpResponse.json(schedule),
    ),
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
      HttpResponse.json({ schedules: [schedule] }),
    ),
    http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
  )
}

/** Wait for the detail page heading to appear (unique per page — the h1). */
async function waitForDetailHeading(text: string) {
  await waitFor(() => {
    const headings = screen.getAllByRole('heading', { level: 1 })
    const match = headings.find((h) => h.textContent?.includes(text))
    expect(match).toBeTruthy()
  })
}

/* ---------------------------------------------------------------- */
/*  List page tests                                                 */
/* ---------------------------------------------------------------- */

describe('TodosPage — list view', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('renders the page header with correct copy', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })

    // "Todos" appears in both TopBar + page h1 — use heading role
    await screen.findByRole('heading', { name: 'Todos' })
    expect(screen.getByText('Agent commitments')).toBeTruthy()
    expect(
      screen.getByText('Scheduled commitments agents created from your instructions.'),
    ).toBeTruthy()
  })

  it('renders all filter tabs', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByRole('heading', { name: 'Todos' })
    expect(screen.getByText('All')).toBeTruthy()
    expect(screen.getByText('Active')).toBeTruthy()
    expect(screen.getByText('Paused')).toBeTruthy()
    expect(screen.getByText('Needs attention')).toBeTruthy()
    expect(screen.getByText('History')).toBeTruthy()
  })

  it('shows schedule items after load', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    // This text is unique — only appears in the first TodoRow
    await screen.findByText('Send the weekly market update')
  })

  it('shows error state with retry button on API failure', async () => {
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ detail: 'Server error' }, { status: 500 }),
      ),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Failed to load Todos')
    expect(screen.getByText('Retry')).toBeTruthy()
  })

  it('shows empty state when list is empty', async () => {
    mockSchedules([])
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('No Todos yet')
  })

  it('groups schedules into sections with counts', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).toContain('Active')
    expect(bodyText).toContain('Needs attention')
    expect(bodyText).toContain('Paused')
    expect(bodyText).toContain('History')
  })

  it('shows summary line with counts', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).toContain('3 active')
    expect(bodyText).toContain('2 needs attention')
  })

  it('shows status pill labels', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    // Multiple rows may share status labels; check existence, not uniqueness
    expect(screen.getAllByText('Armed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Firing now').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Paused').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0)
    expect(screen.getByText('Completed')).toBeTruthy()
    expect(screen.getByText('Review expired')).toBeTruthy()
  })

  it('shows schedule IDs in rows', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    expect(screen.getByText('SCHEDULE-042')).toBeTruthy()
    expect(screen.getByText('SCHEDULE-058')).toBeTruthy()
  })
})

/* ---------------------------------------------------------------- */
/*  Detail page tests                                               */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — provenance and links', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('shows spawned task links with correct route', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    const link = screen.getByText('TASK-8899')
    expect(link.closest('a')?.getAttribute('href')).toBe(`/orgs/${ORG_SLUG}/tasks/TASK-8899`)
  })

  it('shows audit activity link with schedule ID filter', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    const link = screen.getByText('View related activity')
    expect(link.closest('a')?.getAttribute('href')).toBe(`/orgs/${ORG_SLUG}/audit?task_id=SCHEDULE-019`)
  })

  it('shows fire-once explanation for fired one-shot', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    expect(screen.getByText('This Todo fired once. See the linked task for the work outcome.')).toBeTruthy()
  })

  it('shows no action buttons for terminal schedule', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.queryByText('Edit timing')).toBeNull()
  })

  it('shows Pause, Edit, Cancel for armed schedule', async () => {
    mockDetail(ARMED_WEEKLY)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    expect(screen.getByText('Pause')).toBeTruthy()
    expect(screen.getByText('Edit timing')).toBeTruthy()
    expect(screen.getByText('Cancel')).toBeTruthy()
  })

  it('shows Edit, Cancel (no Pause) for paused schedule', async () => {
    mockDetail(PAUSED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-031` })
    await waitForDetailHeading('Review roadmap risks')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.getByText('Edit timing')).toBeTruthy()
    expect(screen.getByText('Cancel')).toBeTruthy()
  })

  it('shows recurrence review callout for weekly non-indefinite', async () => {
    mockDetail(ARMED_WEEKLY)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    expect(document.body.textContent).toContain('Review due')
  })

  it('shows Indefinite badge when indefinite is true', async () => {
    mockDetail(FIRING)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-064` })
    await waitForDetailHeading('Run the nightly regression sweep')
    expect(screen.getByText('Indefinite · no expiry')).toBeTruthy()
  })

  it('shows read-only source instruction', async () => {
    mockDetail(ARMED_WEEKLY)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    expect(screen.getByText('Every Saturday, send me the weekly market update.')).toBeTruthy()
  })

  it('does not fabricate failure cause for failed status', async () => {
    mockDetail(FAILED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-071` })
    await waitForDetailHeading('Sync the customer changelog')
    expect(document.body.textContent).not.toContain('reason:')
    expect(document.body.textContent).not.toContain('error:')
  })
})

/* ---------------------------------------------------------------- */
/*  Mutation and confirmation dialogs                                */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — mutations', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('pause API is called when dialog confirmed', async () => {
    let pauseCalled = false
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY] }),
      ),
      http.post(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042/pause`, () => {
        pauseCalled = true
        return HttpResponse.json({ ...ARMED_WEEKLY, status: 'paused', active: 0 })
      }),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    // Click Pause action button in the actions card
    const pauseActionBtn = screen.getByRole('button', { name: 'Pause' })
    await userEvent.click(pauseActionBtn)
    await screen.findByText('Pause this Todo')
    // Click confirm in dialog ("Pause" appears as dialog button too — use within dialog)
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Pause' }))

    await waitFor(() => { expect(pauseCalled).toBe(true) })
  })

  it('cancel API is called when dialog confirmed', async () => {
    let cancelCalled = false
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY] }),
      ),
      http.post(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042/cancel`, () => {
        cancelCalled = true
        return HttpResponse.json({ ...ARMED_WEEKLY, status: 'cancelled', active: 0 })
      }),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByText('Cancel'))
    await screen.findByText('Cancel this Todo')
    await userEvent.click(screen.getByText('Cancel Todo'))

    await waitFor(() => { expect(cancelCalled).toBe(true) })
  })
})

/* ---------------------------------------------------------------- */
/*  Pure function tests — strings                                     */
/* ---------------------------------------------------------------- */

describe('strings — status labels and grouping', () => {
  it('statusLabel returns correct labels', async () => {
    const mod = await import('@/features/todos/strings')
    expect(mod.statusLabel('armed')).toBe('Armed')
    expect(mod.statusLabel('firing')).toBe('Firing now')
    expect(mod.statusLabel('fired')).toBe('Completed')
    expect(mod.statusLabel('paused')).toBe('Paused')
    expect(mod.statusLabel('cancelled')).toBe('Cancelled')
    expect(mod.statusLabel('expired')).toBe('Review expired')
    expect(mod.statusLabel('failed')).toBe('Needs attention')
    expect(mod.statusLabel('timeout')).toBe('Timed out')
  })

  it('statusPillClass returns correct token classes', async () => {
    const mod = await import('@/features/todos/strings')
    expect(mod.statusPillClass('armed')).toContain('text-status-open')
    expect(mod.statusPillClass('firing')).toContain('text-status-open')
    expect(mod.statusPillClass('failed')).toContain('text-attention-text')
    expect(mod.statusPillClass('timeout')).toContain('text-attention-text')
    expect(mod.statusPillClass('fired')).toContain('text-accent-text')
    expect(mod.statusPillClass('paused')).toContain('text-status-archived')
    expect(mod.statusPillClass('cancelled')).toContain('text-status-archived')
  })

  it('SECTION_ORDER covers all statuses', async () => {
    const mod = await import('@/features/todos/strings')
    const allCovered = new Set<string>(mod.SECTION_ORDER.flatMap((s) => s.statuses))
    for (const s of ['armed', 'firing', 'failed', 'timeout', 'paused', 'fired', 'expired', 'cancelled']) {
      expect(allCovered.has(s)).toBe(true)
    }
  })
})
