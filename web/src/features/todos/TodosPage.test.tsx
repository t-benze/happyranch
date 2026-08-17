/**
 * TodosPage tests — sidebar/routes, filter grouping, action-matrix branches,
 * IANA-timezone rendering, outbound-edit-body correctness, exact provenance
 * URLs, mutation success/failure, and 409 conflict.
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

const ARMED_WEEKLY_TZ: ScheduleRecord = {
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

const ARMED_RECURRING_MONTHLY: ScheduleRecord = {
  schedule_id: 'SCHEDULE-120',
  agent_name: 'investment_advisor',
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
  status: 'armed', active: 1, expires_at: '2026-10-23T00:00:00Z', indefinite: 0,
  spawned_task_ids: [], last_fired_at: null, fire_count: 0,
  created_at: '2026-07-01T00:00:00Z', updated_at: '2026-07-20T00:00:00Z',
}

const ARMED_ONESHOT: ScheduleRecord = {
  schedule_id: 'SCHEDULE-058',
  agent_name: 'support_agent',
  team: 'engineering',
  kind: 'one_shot',
  fire_at: '2026-08-01T18:00:00Z',
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

const ARMED_WEEKLY_TOKYO: ScheduleRecord = {
  schedule_id: 'SCHEDULE-099',
  agent_name: 'investment_advisor',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-08-02T00:00:00Z',
  recurrence: { day: 'Sun', time: '09:00' },
  timezone: 'Asia/Tokyo',
  normalized_brief: 'Tokyo market briefing',
  source_instruction: 'Every Sunday, prepare the Tokyo market briefing.',
  status: 'armed',
  active: 1,
  expires_at: '2026-12-31T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: null,
  fire_count: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-20T00:00:00Z',
}

const ARMED_WEEKLY_NY_DST: ScheduleRecord = {
  schedule_id: 'SCHEDULE-113',
  agent_name: 'support_agent',
  team: 'engineering',
  kind: 'weekly',
  fire_at: '2026-03-01T05:00:00Z',
  recurrence: { day: 'Sun', time: '09:00' },
  timezone: 'America/New_York',
  normalized_brief: 'Prepare the Sunday status brief',
  source_instruction: 'Every Sunday, prepare the status brief.',
  status: 'armed',
  active: 1,
  expires_at: '2026-12-31T00:00:00Z',
  indefinite: 0,
  spawned_task_ids: [],
  last_fired_at: null,
  fire_count: 0,
  created_at: '2026-02-01T00:00:00Z',
  updated_at: '2026-02-15T00:00:00Z',
}

const ALL_SCHEDULES = [
  ARMED_WEEKLY_TZ,
  ARMED_ONESHOT,
  FIRING,
  FAILED,
  TIMEOUT,
  PAUSED,
  CANCELLED,
  FIRED,
  EXPIRED,
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
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/dashboard/summary`, () =>
      HttpResponse.json({ org_age_days: 1 }),
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

function mockDetailWithEdit(
  schedule: ScheduleRecord,
  editHandler?: (body: unknown) => ReturnType<typeof HttpResponse.json>,
) {
  server.use(
    ...bootstrap(),
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/${schedule.schedule_id}`, () =>
      HttpResponse.json(schedule),
    ),
    http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
      HttpResponse.json({ schedules: [schedule] }),
    ),
    http.patch(
      `${API_BASE}/orgs/${ORG_SLUG}/schedules/${schedule.schedule_id}`,
      async ({ request }) => {
        if (editHandler) {
          const body = await request.json()
          return editHandler(body)
        }
        return HttpResponse.json(schedule)
      },
    ),
    http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
  )
}

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
    expect(screen.getAllByText('Armed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Firing now').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Paused').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Cancelled').length).toBeGreaterThan(0)
    expect(screen.getByText('Completed')).toBeTruthy()
    expect(screen.getByText('Review expired')).toBeTruthy()
    expect(screen.getAllByText('Needs attention').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Timed out')).toBeTruthy()
  })

  it('shows schedule IDs as plain text (not as nested links)', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const idSpans = screen.getAllByText('SCHEDULE-042')
    expect(idSpans.length).toBeGreaterThan(0)
    const rowLink = screen.getByText('Send the weekly market update').closest('a')
    expect(rowLink).toBeTruthy()
    expect(rowLink?.getAttribute('href')).toBe(`/orgs/${ORG_SLUG}/todos/SCHEDULE-042`)
  })

  it('rows are navigable Links to the detail route', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const rowLink = screen.getByText('Send the weekly market update').closest('a')
    expect(rowLink?.getAttribute('href')).toBe(`/orgs/${ORG_SLUG}/todos/SCHEDULE-042`)
  })

  it('renders fire_at in the stored IANA timezone (not UTC)', async () => {
    mockSchedules([ARMED_WEEKLY_TZ])
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toMatch(/01:00/)
    expect(bodyText).toContain('09:00')
    expect(bodyText).toContain('Sat')
    expect(bodyText).toContain('Jul 25')
  })

  it('renders one-shot fire_at in America/New_York timezone', async () => {
    mockSchedules([ARMED_ONESHOT])
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Follow up on the Acme trial issue')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).toContain('14:00')
    expect(bodyText).not.toMatch(/18:00/)
  })
})

/* ---------------------------------------------------------------- */
/*  Detail page — timezone rendering                                */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — timezone rendering', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('shows fire_at in stored IANA timezone for weekly schedule', async () => {
    mockDetail(ARMED_WEEKLY_TZ)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).toContain('09:00')
    expect(bodyText).toContain('Asia/Shanghai')
  })

  it('shows fire_at in stored IANA timezone for one-shot schedule', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).toContain('04:00')
    expect(bodyText).toContain('America/Chicago')
  })
})

/* ---------------------------------------------------------------- */
/*  Detail page — indefinite flag                                   */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — indefinite flag', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('does not render a stray "0" for a non-indefinite weekly schedule', async () => {
    mockDetail(ARMED_WEEKLY_TZ)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    expect(screen.queryByText('Indefinite')).not.toBeInTheDocument()
    expect(screen.queryByText('0', { exact: true })).not.toBeInTheDocument()
  })

  it('renders the indefinite callout for a schedule marked indefinite', async () => {
    mockDetail(FIRING)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-064` })
    await waitForDetailHeading('Run the nightly regression sweep')
    expect(screen.getByText('Indefinite')).toBeInTheDocument()
    expect(screen.queryByText('0', { exact: true })).not.toBeInTheDocument()
  })
})

/* ---------------------------------------------------------------- */
/*  Detail page — provenance and links                              */
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
    expect(
      screen.getByText('This Todo fired once. See the linked task for the work outcome.'),
    ).toBeTruthy()
  })

  it('shows no action buttons for terminal schedule', async () => {
    mockDetail(FIRED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-019` })
    await waitForDetailHeading('Check the release health metric')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.queryByText('Edit')).toBeNull()
    expect(screen.queryByText('Cancel')).toBeNull()
  })

  it('shows Pause, Edit, Cancel for armed schedule', async () => {
    mockDetail(ARMED_WEEKLY_TZ)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')
    expect(screen.getByText('Pause')).toBeTruthy()
    expect(screen.getByText('Edit')).toBeTruthy()
    expect(screen.getByText('Cancel')).toBeTruthy()
  })

  it('shows Edit, Cancel (no Pause, no Resume) for paused schedule', async () => {
    mockDetail(PAUSED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-031` })
    await waitForDetailHeading('Review roadmap risks')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.queryByText('Resume')).toBeNull()
    expect(screen.getByText('Edit')).toBeTruthy()
    expect(screen.getByText('Cancel')).toBeTruthy()
  })

  it('shows no actions for firing schedule', async () => {
    mockDetail(FIRING)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-064` })
    await waitForDetailHeading('Run the nightly regression sweep')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.queryByText('Edit timing')).toBeNull()
    expect(screen.queryByText('Cancel')).toBeNull()
  })

  it('shows no actions for failed schedule', async () => {
    mockDetail(FAILED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-071` })
    await waitForDetailHeading('Sync the customer changelog')
    expect(screen.queryByText('Pause')).toBeNull()
    expect(screen.queryByText('Edit timing')).toBeNull()
    expect(screen.queryByText('Cancel')).toBeNull()
  })

  it('shows read-only source instruction', async () => {
    mockDetail(ARMED_WEEKLY_TZ)
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

  it('contains no Resume label or promise anywhere in detail', async () => {
    mockDetail(PAUSED)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-031` })
    await waitForDetailHeading('Review roadmap risks')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toContain('Resume')
    expect(bodyText).not.toContain('resume')
    expect(bodyText).not.toContain('re-activate')
    expect(bodyText).not.toContain('re-arm')
  })

  it('contains no Resume label or promise anywhere in list', async () => {
    mockSchedules(ALL_SCHEDULES)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos` })
    await screen.findByText('Send the weekly market update')
    const bodyText = document.body.textContent ?? ''
    expect(bodyText).not.toContain('Resume')
    expect(bodyText).not.toContain('resume')
    expect(bodyText).not.toContain('re-activate')
    expect(bodyText).not.toContain('re-arm')
  })
})

/* ---------------------------------------------------------------- */
/*  Mutation and confirmation dialogs                               */
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
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.post(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042/pause`, () => {
        pauseCalled = true
        return HttpResponse.json({ ...ARMED_WEEKLY_TZ, status: 'paused', active: 0 })
      }),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByText('Pause this Todo')
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Pause' }))

    await waitFor(() => {
      expect(pauseCalled).toBe(true)
    })
  })

  it('pause dialog does NOT mention resume', async () => {
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.post(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042/pause`, () =>
        HttpResponse.json({ ...ARMED_WEEKLY_TZ, status: 'paused', active: 0 }),
      ),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await screen.findByText('Pause this Todo')
    const dialog = screen.getByRole('dialog')
    const dialogText = dialog.textContent ?? ''
    expect(dialogText).not.toContain('resume')
    expect(dialogText).not.toContain('Resume')
    expect(dialogText).not.toContain('re-activate')
  })

  it('cancel API is called when dialog confirmed', async () => {
    let cancelCalled = false
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.post(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042/cancel`, () => {
        cancelCalled = true
        return HttpResponse.json({ ...ARMED_WEEKLY_TZ, status: 'cancelled', active: 0 })
      }),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByText('Cancel'))
    await screen.findByText('Cancel this Todo')
    await userEvent.click(screen.getByText('Cancel Todo'))

    await waitFor(() => {
      expect(cancelCalled).toBe(true)
    })
  })
})

/* ---------------------------------------------------------------- */
/*  Edit dialog — outbound body correctness                         */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — edit dialog outbound body', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value: () => false,
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: () => undefined,
    })
  })

  it('sends recurrence with tz, top-level timezone matches, and computed fire_at for weekly edit', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_WEEKLY_TZ, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_WEEKLY_TZ)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => {
      expect(capturedBody).not.toBeNull()
    })

    expect(capturedBody!.recurrence).toEqual({
      day: 'Sat',
      time: '09:00',
      tz: 'Asia/Shanghai',
    })
    expect(capturedBody!.timezone).toBe('Asia/Shanghai')
    expect(capturedBody!.fire_at).toBeDefined()
    expect(typeof capturedBody!.fire_at).toBe('string')
    expect((capturedBody!.fire_at as string).endsWith('Z')).toBe(true)
  })

  it('renders native recurring values and saves its rule without a client fire_at', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_RECURRING_MONTHLY, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_RECURRING_MONTHLY)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    expect(screen.getByText(/Every 2 months on the second Monday at 09:00 Asia\/Shanghai/)).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    expect(screen.getByText('Monthly pattern')).toBeTruthy()
    expect(screen.getByText('Named weekday')).toBeTruthy()
    expect(screen.getByText('After count')).toBeTruthy()
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.timezone).toBe('Asia/Shanghai')
    expect(capturedBody!.fire_at).toBeUndefined()
    expect(capturedBody!.recurrence).toEqual({
      freq: 'MONTHLY', interval: 2, time: '09:00', tz: 'Asia/Shanghai',
      until: null, count: 6, byday: ['MO'], bymonthday: null, ordinal: 'second',
    })
  })

  it('submits an optional recurring rephase date without calculating fire_at', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_RECURRING_MONTHLY, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_RECURRING_MONTHLY)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await userEvent.type(screen.getByLabelText('Rephase starting on (optional)'), '2026-09-14')
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.start_date).toBe('2026-09-14')
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('saves a native weekly multi-select recurrence with a positive interval and no end', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_RECURRING_MONTHLY, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_RECURRING_MONTHLY)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })

    const interval = screen.getByLabelText('Repeat every')
    await userEvent.clear(interval)
    await userEvent.type(interval, '3')
    await userEvent.click(screen.getByLabelText('Frequency'))
    await userEvent.click(await screen.findByRole('option', { name: 'week' }))
    expect(screen.getByText('Repeat on')).toBeTruthy()
    await userEvent.click(screen.getByLabelText('Tuesday'))
    await userEvent.click(screen.getByLabelText('Thursday'))
    await userEvent.click(screen.getByLabelText('Never'))
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.recurrence).toEqual({
      freq: 'WEEKLY', interval: 3, time: '09:00', tz: 'Asia/Shanghai',
      until: null, count: null, byday: ['MO', 'TU', 'TH'], bymonthday: null, ordinal: null,
    })
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('saves a native monthly ordinal-to-date transition with its inactive ordinal selectors cleared', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_RECURRING_MONTHLY, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_RECURRING_MONTHLY)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })

    await userEvent.click(screen.getByLabelText('Calendar date'))
    const date = screen.getByLabelText('Date')
    await userEvent.clear(date)
    await userEvent.type(date, '15')
    await userEvent.click(screen.getByLabelText('On date'))
    await userEvent.type(screen.getByLabelText('End date'), '2026-12-31')
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.recurrence).toEqual({
      freq: 'MONTHLY', interval: 2, time: '09:00', tz: 'Asia/Shanghai',
      until: '2026-12-31', count: null, byday: null, bymonthday: 15, ordinal: null,
    })
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('saves a native monthly date-to-ordinal transition with its inactive date selector cleared', async () => {
    let capturedBody: Record<string, unknown> | null = null
    const monthlyDate = {
      ...ARMED_RECURRING_MONTHLY,
      recurrence: {
        freq: 'MONTHLY', interval: 2, bymonthday: 15,
        time: '09:00', tz: 'Asia/Shanghai', until: null, count: 6, anchor_date: '2026-08-10',
      },
    }
    mockDetailWithEdit(monthlyDate, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(monthlyDate)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByLabelText('Named weekday'))
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.recurrence).toEqual({
      freq: 'MONTHLY', interval: 2, time: '09:00', tz: 'Asia/Shanghai',
      until: null, count: 6, byday: ['MO'], bymonthday: null, ordinal: 'first',
    })
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('saves a native weekly-to-daily transition with all selectors explicitly cleared', async () => {
    let capturedBody: Record<string, unknown> | null = null
    const weeklyRecurring = {
      ...ARMED_RECURRING_MONTHLY,
      recurrence: {
        freq: 'WEEKLY', interval: 2, byday: ['MO'],
        time: '09:00', tz: 'Asia/Shanghai', until: null, count: 6, anchor_date: '2026-08-10',
      },
    }
    mockDetailWithEdit(weeklyRecurring, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(weeklyRecurring)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByLabelText('Frequency'))
    await userEvent.click(await screen.findByRole('option', { name: 'day' }))
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.recurrence).toEqual({
      freq: 'DAILY', interval: 2, time: '09:00', tz: 'Asia/Shanghai',
      until: null, count: 6, byday: null, bymonthday: null, ordinal: null,
    })
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('saves a native monthly-to-yearly transition with all selectors explicitly cleared', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_RECURRING_MONTHLY, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_RECURRING_MONTHLY)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-120` })
    await waitForDetailHeading('Review the recurring portfolio allocation')
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByLabelText('Frequency'))
    await userEvent.click(await screen.findByRole('option', { name: 'year' }))
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => expect(capturedBody).not.toBeNull())
    expect(capturedBody!.recurrence).toEqual({
      freq: 'YEARLY', interval: 2, time: '09:00', tz: 'Asia/Shanghai',
      until: null, count: 6, byday: null, bymonthday: null, ordinal: null,
    })
    expect(capturedBody!.fire_at).toBeUndefined()
  })

  it('sends correct recurrence for non-local IANA timezone (Asia/Tokyo)', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_WEEKLY_TOKYO, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_WEEKLY_TOKYO)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-099` })
    await waitForDetailHeading('Tokyo market briefing')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => {
      expect(capturedBody).not.toBeNull()
    })

    expect(capturedBody!.recurrence).toEqual({
      day: 'Sun',
      time: '09:00',
      tz: 'Asia/Tokyo',
    })
    expect(capturedBody!.timezone).toBe('Asia/Tokyo')
    expect(capturedBody!.fire_at).toBeDefined()
  })

  it('serializes one-shot fire_at as the stored-IANA UTC instant, not browser-local time', async () => {
    let capturedBody: Record<string, unknown> | null = null
    mockDetailWithEdit(ARMED_ONESHOT, (body) => {
      capturedBody = body as Record<string, unknown>
      return HttpResponse.json(ARMED_ONESHOT)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-058` })
    await waitForDetailHeading('Follow up on the Acme trial issue')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })

    // Schedule is America/New_York; change the local date/time and save.
    const dateInput = screen.getByLabelText('Date')
    const timeInput = screen.getByLabelText('Time')
    await userEvent.clear(dateInput)
    await userEvent.type(dateInput, '2026-08-05')
    await userEvent.clear(timeInput)
    await userEvent.type(timeInput, '09:00')

    await userEvent.click(screen.getByText('Save changes'))

    await waitFor(() => {
      expect(capturedBody).not.toBeNull()
    })

    // 2026-08-05 09:00 America/New_York (EDT, UTC-4) = 2026-08-05T13:00:00Z
    expect(capturedBody!.fire_at).toBe('2026-08-05T13:00:00Z')
    expect(capturedBody!.timezone).toBe('America/New_York')
    expect((capturedBody!.fire_at as string).endsWith('Z')).toBe(true)
  })

  it('computes nextWeeklyOccurrence correctly for a boundary next-week case', async () => {
    const { nextWeeklyOccurrence } = await import('./timezone')
    const after = new Date('2026-07-31T15:00:00Z')
    const result = nextWeeklyOccurrence('Sat', '09:00', 'Asia/Shanghai', after)
    expect(result).toBe('2026-08-01T01:00:00Z')
  })

  it('computes nextWeeklyOccurrence correctly for same-day-future case', async () => {
    const { nextWeeklyOccurrence } = await import('./timezone')
    const after = new Date('2026-07-31T03:00:00Z')
    const result = nextWeeklyOccurrence('Mon', '09:00', 'Asia/Shanghai', after)
    expect(result).toBe('2026-08-03T01:00:00Z')
  })

  it('computes nextWeeklyOccurrence correctly when today is the target day but time has passed', async () => {
    const { nextWeeklyOccurrence } = await import('./timezone')
    const after = new Date('2026-07-31T02:00:00Z')
    const result = nextWeeklyOccurrence('Fri', '09:00', 'Asia/Shanghai', after)
    expect(result).toBe('2026-08-07T01:00:00Z')
  })

  it('returns null for invalid timezone', async () => {
    const { nextWeeklyOccurrence } = await import('./timezone')
    const result = nextWeeklyOccurrence('Mon', '09:00', 'Invalid/Timezone')
    expect(result).toBeNull()
  })

  it('shows a validation error and does not PATCH when a one-shot falls in a DST gap', async () => {
    let patchCalled = false
    mockDetailWithEdit(ARMED_ONESHOT, () => {
      patchCalled = true
      return HttpResponse.json(ARMED_ONESHOT)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-058` })
    await waitForDetailHeading('Follow up on the Acme trial issue')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })

    const dateInput = screen.getByLabelText('Date')
    const timeInput = screen.getByLabelText('Time')
    await userEvent.clear(dateInput)
    await userEvent.type(dateInput, '2026-03-08')
    await userEvent.clear(timeInput)
    await userEvent.type(timeInput, '02:30')

    await userEvent.click(screen.getByText('Save changes'))

    await screen.findByText(/does not exist in/)
    expect(patchCalled).toBe(false)
  })

  it('shows a validation error and does not PATCH when a weekly recurrence falls in a DST gap', async () => {
    // Fix "now" to 2026-03-02 (Mon) so the next Sunday is the 2026-03-08 DST gap.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-03-02T00:00:00Z'))
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

    let patchCalled = false
    mockDetailWithEdit(ARMED_WEEKLY_NY_DST, () => {
      patchCalled = true
      return HttpResponse.json(ARMED_WEEKLY_NY_DST)
    })

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-113` })
    await waitForDetailHeading('Prepare the Sunday status brief')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })

    // America/New_York springs forward 02:00 -> 03:00 on 2026-03-08.
    // A weekly Sunday 02:30 occurrence does not exist that day.
    const timeInput = screen.getByLabelText('Time')
    await user.clear(timeInput)
    await user.type(timeInput, '02:30')

    await user.click(screen.getByText('Save changes'))

    await screen.findByText(/does not exist in/)
    expect(patchCalled).toBe(false)

    // The entered values must be preserved so the user can correct them.
    expect((timeInput as HTMLInputElement).value).toBe('02:30')

    vi.useRealTimers()
  })

  it('serializeOneShotInTz converts local date/time in non-browser IANA zone to UTC', async () => {
    const { serializeOneShotInTz } = await import('./timezone')
    expect(serializeOneShotInTz('2026-08-05', '09:00', 'Asia/Shanghai')).toBe(
      '2026-08-05T01:00:00Z',
    )
    expect(serializeOneShotInTz('2026-08-05', '09:00', 'America/New_York')).toBe(
      '2026-08-05T13:00:00Z',
    )
    expect(serializeOneShotInTz('2026-08-05', '09:00', 'UTC')).toBe('2026-08-05T09:00:00Z')
  })

  it('serializeOneShotInTz rejects nonexistent DST-gap local times', async () => {
    const { serializeOneShotInTz } = await import('./timezone')
    // America/New_York springs forward at 02:00 → 03:00 on 2026-03-08.
    expect(serializeOneShotInTz('2026-03-08', '02:30', 'America/New_York')).toBeNull()
    expect(serializeOneShotInTz('2026-03-08', '02:01', 'America/New_York')).toBeNull()
    expect(serializeOneShotInTz('2026-03-08', '02:59', 'America/New_York')).toBeNull()
    // Europe/Berlin springs forward at 02:00 → 03:00 on 2026-03-29.
    expect(serializeOneShotInTz('2026-03-29', '02:30', 'Europe/Berlin')).toBeNull()
  })

  it('serializeOneShotInTz accepts valid times immediately adjacent to a DST gap', async () => {
    const { serializeOneShotInTz } = await import('./timezone')
    // 01:59 EST exists; 03:00 EDT exists.
    expect(serializeOneShotInTz('2026-03-08', '01:59', 'America/New_York')).toBe(
      '2026-03-08T06:59:00Z',
    )
    expect(serializeOneShotInTz('2026-03-08', '03:00', 'America/New_York')).toBe(
      '2026-03-08T07:00:00Z',
    )
  })

  it('serializeOneShotInTz accepts ambiguous DST-fold local times and returns a verified instant', async () => {
    const { serializeOneShotInTz, formatFireAtInTz } = await import('./timezone')
    // America/New_York falls back at 02:00 → 01:00 on 2026-11-01, so 01:30 occurs twice.
    const iso = serializeOneShotInTz('2026-11-01', '01:30', 'America/New_York')
    expect(iso).not.toBeNull()
    // The returned instant must render back to the requested wall time.
    expect(formatFireAtInTz(iso!, 'America/New_York')).toContain('01:30')
    expect(formatFireAtInTz(iso!, 'America/New_York')).toContain('Nov 1')
  })
})

/* ---------------------------------------------------------------- */
/*  Edit dialog — 409 conflict                                      */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — 409 conflict handling', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('shows conflict reload prompt on 409 response', async () => {
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.patch(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(
          {
            code: 'state_conflict',
            message: 'cannot edit SCHEDULE-042: status firing is not armed or paused',
          },
          { status: 409 },
        ),
      ),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByText('Save changes'))

    await screen.findByText('This Todo was modified')
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
  })

  it('409 conflict prompt uses neutral wording and does not claim the Todo fired', async () => {
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.patch(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(
          {
            code: 'state_conflict',
            message: 'cannot edit SCHEDULE-042: status firing is not armed or paused',
          },
          { status: 409 },
        ),
      ),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByText('Save changes'))

    const conflictDialog = await screen.findByRole('dialog')
    const dialogText = conflictDialog.textContent ?? ''
    expect(dialogText).toContain('This Todo changed while you were editing it')
    expect(dialogText).not.toContain('most likely it fired')
    expect(dialogText).not.toContain('fired')
    expect(dialogText).not.toContain('saved')
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()
  })

  it('shows validation error inline on non-409 error', async () => {
    server.use(
      ...bootstrap(),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(ARMED_WEEKLY_TZ),
      ),
      http.get(`${API_BASE}/orgs/${ORG_SLUG}/schedules`, () =>
        HttpResponse.json({ schedules: [ARMED_WEEKLY_TZ] }),
      ),
      http.patch(`${API_BASE}/orgs/${ORG_SLUG}/schedules/SCHEDULE-042`, () =>
        HttpResponse.json(
          { detail: 'fire_at must be in the future' },
          { status: 422 },
        ),
      ),
      http.all(`${API_BASE}/*`, () => HttpResponse.json({})),
    )

    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await screen.findByRole('heading', { name: 'Edit timing' })
    await userEvent.click(screen.getByText('Save changes'))

    await screen.findByText('fire_at must be in the future')
    expect(screen.queryByText('Reload record')).toBeNull()
    expect(screen.getByRole('heading', { name: 'Edit timing' })).toBeTruthy()
  })
})

/* ---------------------------------------------------------------- */
/*  Pure function tests — strings                                   */
/* ---------------------------------------------------------------- */

describe('strings — status labels and grouping', () => {
  it('statusLabel returns correct labels', async () => {
    const mod = await import('./strings')
    expect(mod.statusLabel('armed')).toBe('Armed')
    expect(mod.statusLabel('firing')).toBe('Firing now')
    expect(mod.statusLabel('fired')).toBe('Completed')
    expect(mod.statusLabel('paused')).toBe('Paused')
    expect(mod.statusLabel('cancelled')).toBe('Cancelled')
    expect(mod.statusLabel('expired')).toBe('Review expired')
    expect(mod.statusLabel('failed')).toBe('Needs attention')
    expect(mod.statusLabel('timeout')).toBe('Timed out')
  })

  it('statusPillClass returns Todos-local reference colors', async () => {
    const mod = await import('./strings')
    expect(mod.statusPillClass('armed')).toContain('text-[#2b5c3a]')
    expect(mod.statusPillClass('armed')).toContain('bg-[#e3efe5]')
    expect(mod.statusPillClass('firing')).toContain('text-[#2b5c3a]')
    expect(mod.statusPillClass('fired')).toContain('text-[#2b5c3a]')
    expect(mod.statusPillClass('failed')).toContain('text-[#575249]')
    expect(mod.statusPillClass('failed')).toContain('bg-[#f3e8d6]')
    expect(mod.statusPillClass('timeout')).toContain('bg-[#f3e8d6]')
    expect(mod.statusPillClass('paused')).toContain('text-[#575249]')
    expect(mod.statusPillClass('paused')).toContain('bg-[#efece2]')
    expect(mod.statusPillClass('cancelled')).toContain('bg-[#efece2]')
    expect(mod.statusPillClass('expired')).toContain('bg-[#efece2]')
  })

  it('SECTION_ORDER covers all statuses', async () => {
    const mod = await import('./strings')
    const allCovered = new Set<string>(mod.SECTION_ORDER.flatMap((s) => s.statuses))
    for (const s of [
      'armed',
      'firing',
      'failed',
      'timeout',
      'paused',
      'fired',
      'expired',
      'cancelled',
    ]) {
      expect(allCovered.has(s)).toBe(true)
    }
  })
})

/* ---------------------------------------------------------------- */
/*  timezone utilities                                              */
/* ---------------------------------------------------------------- */

describe('timezone utilities', () => {
  it('formatFireAtInTz renders in the target IANA timezone', async () => {
    const { formatFireAtInTz } = await import('./timezone')
    const result = formatFireAtInTz('2026-07-25T01:00:00Z', 'Asia/Shanghai')
    expect(result).toContain('09:00')
    expect(result).toContain('Sat')
    expect(result).toContain('Jul 25')
    expect(result).toContain('2026')
  })

  it('formatFireAtInTz handles America/New_York timezone', async () => {
    const { formatFireAtInTz } = await import('./timezone')
    const result = formatFireAtInTz('2026-08-01T18:00:00Z', 'America/New_York')
    expect(result).toContain('14:00')
    expect(result).not.toContain('18:00')
  })

  it('formatFireAtInTz handles America/Chicago timezone', async () => {
    const { formatFireAtInTz } = await import('./timezone')
    const result = formatFireAtInTz('2026-07-20T09:00:00Z', 'America/Chicago')
    expect(result).toContain('04:00')
  })

  it('formatPreviewInTz renders a Date in target timezone', async () => {
    const { formatPreviewInTz } = await import('./timezone')
    const d = new Date('2026-08-01T01:00:00Z')
    const result = formatPreviewInTz(d, 'Asia/Shanghai')
    expect(result).toContain('09:00')
    expect(result).toContain('Sat')
    expect(result).toContain('2026')
  })
})

/* ---------------------------------------------------------------- */
/*  Back navigation                                                 */
/* ---------------------------------------------------------------- */

describe('TodoDetailPage — back navigation', () => {
  beforeEach(() => {
    sessionStorage.setItem('happyranch.token', 'mock-token')
  })

  it('shows Back link that points to the todos list route', async () => {
    mockDetail(ARMED_WEEKLY_TZ)
    renderWithProviders(<AppRoutes />, { route: `/orgs/${ORG_SLUG}/todos/SCHEDULE-042` })
    await waitForDetailHeading('Send the weekly market update')

    const backLinks = screen.getAllByRole('link', { name: /Todos/i })
    const backLink = backLinks.find((el) => el.getAttribute('href') === `/orgs/${ORG_SLUG}/todos`)
    expect(backLink).toBeDefined()
  })
})
