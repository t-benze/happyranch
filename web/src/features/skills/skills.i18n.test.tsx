/**
 * THR-118 W4c — Skills route family i18n.
 *
 * Renders the real routed Skills pages under the real I18nProvider and asserts:
 *  - zh-CN product chrome on every `skills*` route (catalog, runtime validation,
 *    custom list, custom create, custom detail, skill detail) including the
 *    empty and error states;
 *  - daemon values (skill names/slugs/summaries/descriptions, versions, agent
 *    names, SKILL.md bodies, content hashes, source paths) stay byte-verbatim;
 *  - a locale switch re-translates the same nodes in place;
 *  - count-bearing chrome goes through plural catalog objects (en one/other);
 *  - the `classifySkillError` diagnostic boundary: a recognized code maps to a
 *    catalog key, an unknown code and a code-less string detail stay verbatim,
 *    and an empty code falls back to the localized copy.
 *
 * features/skills owns no Dialog/DialogContent (the purge confirm is an inline
 * `role="dialog"` section with Cancel, not a design-system Dialog), so there is
 * no closeLabel to assert here.
 */
import { act, fireEvent, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, test } from 'vitest'
import { AppRoutes } from '@/routes'
import { LocaleTestSwitch, renderWithProviders, savedLocaleAdapter } from '@/test/render'
import { server } from '@/test/server'
import { translate, type MessageKey } from '@/lib/i18n'
import { formatDateShapeFor } from '@/lib/i18n/format'

const SLUG = 'alpha'
const API = `/api/v1/orgs/${SLUG}`
const CUSTOM_ID = 'custom:playbook'

const CATALOG_ITEMS = [
  {
    skill_id: 'c1',
    name: 'founder-escalation-protocol',
    type: 'system_contract',
    source: 'bundled',
    system_contract: true,
    visibility_category: 'read_only',
    policy_class: 'guidance',
    status: 'active',
    version: 'locked',
    validation_state: 'validated',
    assigned_agent_count: 5,
    effective_agent_count: 5,
    has_assigned_not_yet_effective: false,
    summary: 'Escalate to the founder verbatim summary.',
  },
  {
    skill_id: 'u1',
    name: 'vendor-comms-style',
    type: 'user_authored',
    source: 'custom',
    system_contract: false,
    visibility_category: 'toggleable',
    policy_class: 'guidance',
    status: 'active',
    version: '0.3.0',
    validation_state: 'failed_validation',
    assigned_agent_count: 2,
    effective_agent_count: 1,
    has_assigned_not_yet_effective: true,
    summary: 'Vendor tone summary.',
  },
]

const DETAIL_BUNDLED = {
  skill_id: 'm1',
  name: 'kb-curation',
  type: 'managed',
  source: 'runtime/skills/bundled/kb-curation',
  system_contract: false,
  visibility_category: 'toggleable',
  policy_class: 'guidance',
  status: 'active',
  version: '2.1.0',
  validation_state: 'validated',
  summary: 'Curate the KB.',
  description: 'Raw SKILL.md body text.',
  when_to_use: 'When curating.',
  owner: 'platform',
  assignments: [
    {
      agent: 'research_lead',
      assigned: true,
      effective: true,
      state: 'effective',
    },
    {
      agent: 'support_agent',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    },
  ],
}

const CUSTOM = {
  skill_id: CUSTOM_ID,
  slug: 'playbook',
  name: 'Partner playbook',
  description: 'Founder-authored guidance.',
  current_version_id: 3,
  retired_at: null,
  validation_state: 'valid',
  content_hash: 'sha256:abc123',
  skill_md_cache: '# Partner playbook body',
}

const EVENTS = [
  {
    id: 1,
    skill_id: 'sk-vendor',
    slug: 'vendor-comms-style',
    agent: 'vendor_desk',
    source: 'user_authored',
    severity: 'error',
    ok: false,
    version: '0.3.0',
    findings: ['SKILL.md raw finding.'],
    reason_codes: ['missing_version', 'brand_new_code'],
    created_at: '2026-07-15T09:00:00Z',
  },
  {
    id: 2,
    skill_id: 'sk-contract',
    slug: 'founder-escalation-protocol',
    agent: null,
    source: 'materialization',
    severity: 'info',
    ok: true,
    version: 'locked',
    findings: [],
    reason_codes: [],
    created_at: '2026-07-15T09:00:00Z',
  },
]

function shell() {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] })),
    http.get(`${API}/dashboard/summary`, () => HttpResponse.json({})),
  )
}

function mount(locale: 'en' | 'zh-CN', route: string) {
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

beforeEach(() => {
  sessionStorage.setItem('happyranch.token', 'tok')
  localStorage.clear()
  shell()
})

describe('Skills catalog i18n', () => {
  test('populated catalog: zh-CN chrome, verbatim daemon values, plural count, in-place switch', async () => {
    server.use(
      http.get(`${API}/skills/catalog`, () => HttpResponse.json({ items: CATALOG_ITEMS })),
      http.get(`${API}/custom-skills/catalog`, () => HttpResponse.json({ skills: [] })),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills`)
    expect(await screen.findByText('founder-escalation-protocol')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '智能体可使用的指导' })).toBeInTheDocument()
    expect(screen.getByText('全部技能 · 2')).toBeInTheDocument()
    expect(screen.getByText('1 项需要处理')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '内置' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('button', { name: '自定义' }).length).toBeGreaterThan(0)
    expect(screen.getByText('运行时验证')).toBeInTheDocument()
    expect(screen.getByText('添加自定义技能')).toBeInTheDocument()
    expect(screen.getByText('仅影响指导可见性。')).toBeInTheDocument()
    expect(screen.getByText('系统契约')).toBeInTheDocument()
    expect(screen.getByText('需要处理')).toBeInTheDocument()
    expect(screen.getByText('已验证')).toBeInTheDocument()
    expect(screen.getAllByText('已分配').length).toBe(2)
    expect(screen.getByText('下次会话生效')).toBeInTheDocument()
    expect(screen.getByText('只读 — 无法编辑或取消分配')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看 vendor-comms-style' })).toBeInTheDocument()
    // Daemon values verbatim.
    expect(screen.getByText('Escalate to the founder verbatim summary.')).toBeInTheDocument()
    expect(screen.getByText('vlocked')).toBeInTheDocument()
    expect(screen.getByText('v0.3.0')).toBeInTheDocument()

    const heading = screen.getByRole('heading', { name: '智能体可使用的指导' })
    await switchLocale('en')
    expect(heading).toHaveTextContent('Guidance your agents can use')
    expect(screen.getByText('1 needs attention')).toBeInTheDocument()
    expect(screen.getByText('All skills · 2')).toBeInTheDocument()
  })

  test('empty and error states are localized', async () => {
    server.use(http.get(`${API}/skills/catalog`, () => HttpResponse.json({ items: [] })))
    const { unmount } = mount('zh-CN', `/orgs/${SLUG}/skills`)
    expect(await screen.findByText('这里还没有技能')).toBeInTheDocument()
    expect(screen.getByText('没有符合此来源的技能。')).toBeInTheDocument()
    unmount()
    server.use(http.get(`${API}/skills/catalog`, () => new HttpResponse(null, { status: 500 })))
    mount('zh-CN', `/orgs/${SLUG}/skills`)
    expect(await screen.findByText('无法加载技能')).toBeInTheDocument()
    expect(screen.getByText('技能目录暂时不可用，请稍后重试。')).toBeInTheDocument()
  })
})

describe('Skill detail i18n', () => {
  test('bundled detail: zh-CN chrome, provenance labels and verbatim values', async () => {
    server.use(http.get(`${API}/skills/catalog/m1`, () => HttpResponse.json(DETAIL_BUNDLED)))
    mount('zh-CN', `/orgs/${SLUG}/skills/m1`)
    expect(await screen.findByRole('heading', { name: 'kb-curation' })).toBeInTheDocument()
    expect(screen.getByText('返回技能')).toBeInTheDocument()
    expect(screen.getByText('内置')).toBeInTheDocument()
    expect(screen.getByText('只读')).toBeInTheDocument()
    expect(screen.getByText('内置技能 — 其指导由平台管理，无法在此编辑。')).toBeInTheDocument()
    expect(screen.getByText('使用时机')).toBeInTheDocument()
    expect(screen.getByText('指导（SKILL.md）')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '此技能在哪些地方生效' })).toBeInTheDocument()
    expect(screen.getByText('1 个将在下次会话生效')).toBeInTheDocument()
    // Rollup label + the effective agent's chip.
    expect(screen.getAllByText('已生效').length).toBe(2)
    expect(screen.getByText('当前版本已作为指导展示给该智能体。')).toBeInTheDocument()
    expect(screen.getByText('已分配 — 当前版本将在该智能体的下次会话生效。')).toBeInTheDocument()
    // Daemon values verbatim.
    expect(screen.getByText('runtime/skills/bundled/kb-curation')).toBeInTheDocument()
    expect(screen.getByText('Raw SKILL.md body text.')).toBeInTheDocument()
    expect(screen.getByText('research_lead')).toBeInTheDocument()
    expect(screen.getByText('v2.1.0')).toBeInTheDocument()
  })

  test('load error: localized title, recognized not_found code maps to catalog copy', async () => {
    server.use(
      http.get(`${API}/skills/catalog/gone`, () =>
        HttpResponse.json({ detail: { code: 'not_found' } }, { status: 404 }),
      ),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills/gone`)
    expect(await screen.findByText('无法加载此技能')).toBeInTheDocument()
    expect(screen.getByText('此技能暂时不可用，或链接已过期。')).toBeInTheDocument()
  })
})

describe('Runtime validation i18n', () => {
  test('populated: zh-CN filters, badges, reason copy, relative age; unknown code and findings verbatim', async () => {
    server.use(
      http.get(`${API}/skills/validation`, () =>
        HttpResponse.json({ events: EVENTS, label: 'Runtime Validation' }),
      ),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills/validation`)
    expect(await screen.findByRole('heading', { name: '运行时验证' })).toBeInTheDocument()
    expect((await screen.findAllByText('vendor-comms-style')).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('combobox', { name: '技能' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('option', { name: '全部来源' }).length).toBeGreaterThan(0)
    expect(screen.getAllByRole('option', { name: '最近 7 天' }).length).toBeGreaterThan(0)
    expect(screen.getByText('技能指南缺少版本。')).toBeInTheDocument()
    expect(screen.getByText('按上下文应用 — 全部智能体')).toBeInTheDocument()
    // Row badges (spans), distinct from the same words in the filter <option>s.
    expect(screen.getByText('会话启动时应用', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('信息', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getAllByText(/^\d+ 天$/).length).toBe(2)
    // Daemon values verbatim.
    expect(screen.getByText('SKILL.md raw finding.')).toBeInTheDocument()
    expect(screen.getByText('vendor_desk', { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('v0.3.0')).toBeInTheDocument()
  })

  test('empty and error states are localized', async () => {
    server.use(http.get(`${API}/skills/validation`, () => HttpResponse.json({ events: [] })))
    const { unmount } = mount('zh-CN', `/orgs/${SLUG}/skills/validation`)
    expect(await screen.findByText('还没有运行时验证事件')).toBeInTheDocument()
    unmount()
    server.use(http.get(`${API}/skills/validation`, () => new HttpResponse(null, { status: 500 })))
    mount('zh-CN', `/orgs/${SLUG}/skills/validation`)
    expect(await screen.findByText('无法加载运行时验证')).toBeInTheDocument()
    expect(screen.getByText('这些事件暂时不可用，请稍后重试。')).toBeInTheDocument()
  })
})

describe('Custom skills i18n', () => {
  test('list populated, empty and error states', async () => {
    server.use(http.get(`${API}/custom-skills/catalog`, () => HttpResponse.json({ skills: [CUSTOM] })))
    const first = mount('zh-CN', `/orgs/${SLUG}/skills/custom`)
    expect(await screen.findByText('Partner playbook')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '自定义技能' })).toBeInTheDocument()
    expect(screen.getByText('创始人工作区 · 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '当前' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已移除' })).toBeInTheDocument()
    expect(screen.getByText('Founder-authored guidance.')).toBeInTheDocument()
    expect(screen.getByText('v3')).toBeInTheDocument()
    first.unmount()

    server.use(http.get(`${API}/custom-skills/catalog`, () => HttpResponse.json({ skills: [] })))
    const second = mount('zh-CN', `/orgs/${SLUG}/skills/custom`)
    expect(await screen.findByText('还没有自定义技能')).toBeInTheDocument()
    second.unmount()

    server.use(http.get(`${API}/custom-skills/catalog`, () => new HttpResponse(null, { status: 500 })))
    mount('zh-CN', `/orgs/${SLUG}/skills/custom`)
    expect(await screen.findByText('无法加载自定义技能')).toBeInTheDocument()
    expect(screen.getByText('自定义技能目录暂时不可用，请稍后重试。')).toBeInTheDocument()
  })

  test('create page chrome and a recognized invalid_slug error re-translates in place', async () => {
    server.use(
      http.post(`${API}/custom-skills`, () =>
        HttpResponse.json({ detail: { code: 'invalid_slug' } }, { status: 422 }),
      ),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills/custom/new`)
    expect(await screen.findByRole('heading', { name: '创建自定义技能' })).toBeInTheDocument()
    expect(screen.getByText('返回自定义技能')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('标识'), {
      target: { value: 'Bad Slug' },
    })
    fireEvent.change(screen.getByLabelText('SKILL.md'), {
      target: { value: '# x' },
    })
    fireEvent.click(screen.getByRole('button', { name: '创建自定义技能' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('标识只能使用小写 ASCII 字母')
    await switchLocale('en')
    expect(alert).toHaveTextContent('lower-case ASCII letters a-z')
  })

  test('custom detail chrome, verbatim values and an unknown-code mutation error', async () => {
    server.use(
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}`, () => HttpResponse.json(CUSTOM)),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/versions`, () =>
        HttpResponse.json({
          versions: [
            {
              id: 3,
              content_hash: 'sha256:abc123',
              created_at: '2026-08-01T00:00:00Z',
              validation_state: 'valid',
            },
          ],
        }),
      ),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/eligibility`, () =>
        HttpResponse.json({
          rules: [{ scope_type: 'agent', scope_target: 'ada', effect: 'allow' }],
          revision: 4,
        }),
      ),
      http.put(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/eligibility`, () =>
        HttpResponse.json({ detail: { code: 'eligibility_brand_new' } }, { status: 422 }),
      ),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills/custom/${encodeURIComponent(CUSTOM_ID)}`)
    expect(await screen.findByRole('heading', { name: 'Partner playbook' })).toBeInTheDocument()
    expect(screen.getByText('元数据')).toBeInTheDocument()
    expect(screen.getByText('当前指导 / SKILL.md')).toBeInTheDocument()
    expect(screen.getByText('当前修订：v4')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '规则 1 范围' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停用' })).toBeInTheDocument()
    expect(screen.getByText('版本历史')).toBeInTheDocument()
    // Daemon values verbatim.
    expect(screen.getByText('# Partner playbook body')).toBeInTheDocument()
    expect(screen.getAllByText('sha256:abc123').length).toBeGreaterThan(0)
    expect(screen.getByText('v3 · valid')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '保存资格' }))
    const status = await screen.findByText('无法保存资格。（eligibility_brand_new）')
    await switchLocale('en')
    expect(status).toHaveTextContent('Could not save eligibility. (eligibility_brand_new)')
  })

  test('custom detail load error is localized', async () => {
    server.use(
      http.get(
        `${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}`,
        () => new HttpResponse(null, { status: 500 }),
      ),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/versions`, () =>
        HttpResponse.json({ versions: [] }),
      ),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/eligibility`, () =>
        HttpResponse.json({ rules: [], revision: 1 }),
      ),
    )
    mount('zh-CN', `/orgs/${SLUG}/skills/custom/${encodeURIComponent(CUSTOM_ID)}`)
    expect(await screen.findByText('无法加载此自定义技能')).toBeInTheDocument()
    expect(screen.getByText('此自定义技能暂时不可用，请稍后重试。')).toBeInTheDocument()
  })
})

describe('Permanently removed custom skill i18n', () => {
  // Noon UTC keeps the calendar day stable in every host timezone.
  const PURGED_AT = '2026-08-30T12:02:03Z'

  test.each(['en', 'zh-CN'] as const)('%s purge completion time renders through formatDateShapeFor', async (locale) => {
    server.use(
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}`, () =>
        HttpResponse.json({ ...CUSTOM, state: 'permanently_removed', hidden_reason: 'purged', purge_id: 'PURGE-7', purged_at: PURGED_AT }),
      ),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/versions`, () => HttpResponse.json({ versions: [] })),
      http.get(`${API}/custom-skills/${encodeURIComponent(CUSTOM_ID)}/eligibility`, () =>
        HttpResponse.json({ rules: [], revision: 1 }),
      ),
    )
    mount(locale, `/orgs/${SLUG}/skills/custom/${encodeURIComponent(CUSTOM_ID)}`)
    expect(await screen.findByRole('heading', { name: translate(locale, 'skills.status.permanentlyRemoved') })).toBeInTheDocument()
    const completed = formatDateShapeFor(locale, new Date(PURGED_AT), 'dateTime')
    expect(completed).toContain(locale === 'en' ? 'Aug 30, 2026' : '2026年8月30日')
    const label = screen.getByText(translate(locale, 'skills.customDetail.completed'))
    expect(label.tagName).toBe('DT')
    expect(label.nextElementSibling?.textContent).toBe(completed)
    expect(screen.getByText('PURGE-7')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain(PURGED_AT)
  })
})

describe('classifySkillError boundary', () => {
  test('recognized code → key; unknown code raw; code-less string detail verbatim; empty → fallback', async () => {
    // Variable specifier so a missing module fails THIS test, not the whole file.
    const stringsModule = './strings'
    const { classifySkillError, renderSkillError } = (await import(
      /* @vite-ignore */ stringsModule
    )) as typeof import('./strings')
    const fallback: MessageKey = 'skills.catalog.errorBody'
    const t = (key: MessageKey) => translate('zh-CN', key)
    expect(classifySkillError({ code: 'invalid_slug' }, fallback)).toEqual({
      kind: 'message',
      key: 'skills.create.error.invalidSlug',
    })
    expect(renderSkillError(classifySkillError({ code: 'brand_new_code' }, fallback), t)).toBe(
      'brand_new_code',
    )
    expect(renderSkillError(classifySkillError({ code: null, detail: 'daemon said no' }, fallback), t)).toBe(
      'daemon said no',
    )
    expect(renderSkillError(classifySkillError(new Error('boom'), fallback), t)).toBe('boom')
    expect(renderSkillError(classifySkillError('thrown text', fallback), t)).toBe('thrown text')
    expect(renderSkillError(classifySkillError({ code: '' }, fallback), t)).toBe(
      '技能目录暂时不可用，请稍后重试。',
    )
    expect(renderSkillError(classifySkillError({ code: null, detail: '   ' }, fallback), t)).toBe(
      '技能目录暂时不可用，请稍后重试。',
    )
  })

  test('plural catalog entries select en one/other', () => {
    expect(translate('en', 'skills.catalog.needsAttention', { count: 1 })).toBe('1 needs attention')
    expect(translate('en', 'skills.catalog.needsAttention', { count: 3 })).toBe('3 need attention')
    expect(translate('en', 'skills.assign.apply', { count: 1 })).toBe('Apply 1 change')
    expect(translate('en', 'skills.assign.apply', { count: 2 })).toBe('Apply 2 changes')
    expect(translate('zh-CN', 'skills.assign.apply', { count: 2 })).toBe('应用 2 项更改')
  })
})
