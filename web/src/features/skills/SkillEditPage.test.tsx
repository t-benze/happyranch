import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

// An editable custom skill with two effective agents + one already-not-yet-
// effective agent — exercises the edited-effective transform on a version bump.
const CUSTOM_ID = 'sk-tourism-partner-playbook';
const CUSTOM_DETAIL = {
  skill_id: CUSTOM_ID,
  name: 'tourism-partner-playbook',
  type: 'user_authored',
  source: 'custom · store/tourism-partner-playbook/SKILL.md',
  system_contract: false,
  visibility_category: 'toggleable',
  policy_class: 'guidance',
  status: 'enabled',
  version: '1.2.0',
  validation_state: 'validated',
  summary: 'House style for briefing partner venues.',
  description: 'The house style for partner-venue briefings.',
  when_to_use: 'When drafting a partner brief.',
  owner: 'operator',
  validation: { ok: true, errors: [] },
  assignments: [
    { agent: 'partner_liaison', assigned: true, effective: true, state: 'effective' },
    { agent: 'itinerary_planner', assigned: true, effective: true, state: 'effective' },
    {
      agent: 'support_agent',
      assigned: true,
      effective: false,
      state: 'assigned_not_yet_effective',
    },
  ],
};

// A read-only bundled skill — reaching /edit directly must NOT render the form.
const BUNDLED_ID = 'sk-kb-curation';
const BUNDLED_DETAIL = {
  ...CUSTOM_DETAIL,
  skill_id: BUNDLED_ID,
  name: 'kb-curation',
  type: 'managed',
  source: 'bundled · skills/kb-curation/SKILL.md',
  assignments: [],
};

interface EditResponse {
  skill_id: string;
  source: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  validation: { ok: boolean; errors: string[] };
  version: string;
}

const PASS_BUMP: EditResponse = {
  skill_id: CUSTOM_ID,
  source: 'user_authored',
  validation_state: 'validated',
  validation: { ok: true, errors: [] },
  version: '1.3.0',
};

const FAIL: EditResponse = {
  skill_id: CUSTOM_ID,
  source: 'user_authored',
  validation_state: 'in_catalog',
  validation: {
    ok: false,
    errors: [
      'version is required',
      'The references/pricing.md asset could not be resolved.',
    ],
  },
  version: '1.2.0',
};

function mount(
  detail: typeof CUSTOM_DETAIL,
  opts: { editResponse?: EditResponse; editStatus?: number; skillId?: string } = {},
) {
  const skillId = opts.skillId ?? CUSTOM_ID;
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/skills/catalog/:id`, () =>
      HttpResponse.json(detail),
    ),
    http.patch(`/api/v1/orgs/${SLUG}/skills/:id`, () =>
      HttpResponse.json(opts.editResponse ?? PASS_BUMP, {
        status: opts.editStatus ?? 200,
      }),
    ),
    http.post(`/api/v1/orgs/${SLUG}/skills/:id/validate`, ({ params }) =>
      HttpResponse.json(
        {
          skill_id: params.id,
          validation_state: 'validated',
          validation: { ok: true, errors: [] },
        },
        { status: 200 },
      ),
    ),
  );
  return renderWithProviders(<AppRoutes />, {
    route: `/orgs/${SLUG}/skills/${skillId}/edit`,
  });
}

describe('SkillEditPage — edit + re-validate a custom skill (THR-092 Slice 4)', () => {
  test('prefills name / summary / version from the detail; no policy_class control; SKILL.md is blank (keeps current)', async () => {
    mount(CUSTOM_DETAIL);
    expect(
      await screen.findByRole('heading', { name: /Edit a custom skill/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^Name/i)).toHaveValue('tourism-partner-playbook');
    expect(screen.getByLabelText(/Version/i)).toHaveValue('1.2.0');
    expect(screen.getByLabelText(/Summary/i)).toHaveValue(
      'House style for briefing partner venues.',
    );
    // Blank SKILL.md keeps the current guidance (daemon preserves it on omit).
    expect(screen.getByLabelText(/SKILL\.md/i)).toHaveValue('');
    // A custom skill can NEVER mint / alter a system_contract.
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/policy.?class/i);
    expect(main).not.toMatch(/system.?contract/i);
  });

  test('custom-only gating: a read-only bundled skill reached at /edit shows no form', async () => {
    mount(BUNDLED_DETAIL, { skillId: BUNDLED_ID });
    expect(
      await screen.findByText(/This skill is read-only/i),
    ).toBeInTheDocument();
    // No editable form, no submit.
    expect(screen.queryByLabelText(/SKILL\.md/i)).toBeNull();
    expect(
      screen.queryByRole('button', { name: /Save & re-validate/i }),
    ).toBeNull();
    // Still offers a way back to the detail surface.
    expect(screen.getByRole('link', { name: /View skill/i })).toBeInTheDocument();
  });

  test('required-field guard blocks submit when the name is cleared', async () => {
    mount(CUSTOM_DETAIL);
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.clear(screen.getByLabelText(/^Name/i));
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    expect(await screen.findByText('Add a name.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('PASS + version bump: PATCH submits, confirms next-session effect, and shows every already-effective agent as "takes effect next session"', async () => {
    mount(CUSTOM_DETAIL, { editResponse: PASS_BUMP });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.clear(screen.getByLabelText(/Version/i));
    await userEvent.type(screen.getByLabelText(/Version/i), '1.3.0');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'validated');
    expect(within(result).getByText('Validated')).toBeInTheDocument();
    expect(within(result).getByText(/takes effect for each assigned agent at its next session/i)).toBeInTheDocument();
    // The two previously-effective agents are now assigned-not-yet-effective.
    const liaison = within(result).getByText('partner_liaison').closest('li')!;
    expect(liaison).toHaveAttribute('data-status', 'not_yet_effective');
    expect(within(liaison).getByText('Takes effect next session')).toBeInTheDocument();
    const planner = within(result).getByText('itinerary_planner').closest('li')!;
    expect(planner).toHaveAttribute('data-status', 'not_yet_effective');
    // View skill points at the Slice-2 detail route; no Re-validate on success.
    expect(within(result).getByRole('link', { name: /View skill/i })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/${CUSTOM_ID}`,
    );
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('FAILURE (draft-persist): maps issues + explains every check + keeps an editable draft + offers View skill / Re-validate', async () => {
    mount(CUSTOM_DETAIL, { editResponse: FAIL });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# fail this validation');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'failed_validation');
    expect(within(result).getByText('Needs attention')).toBeInTheDocument();
    // Draft-persist framing — not a dead end.
    expect(within(result).getByText(/not a review gate/i)).toBeInTheDocument();
    expect(within(result).getByText(/draft is kept in the catalog/i)).toBeInTheDocument();
    // Raw backend errors mapped to plain-language guidance.
    expect(within(result).getByText(/Add a version to the skill’s details/i)).toBeInTheDocument();
    expect(within(result).getByText(/referenced file couldn’t be found/i)).toBeInTheDocument();
    // The plain-language explanation covers every check.
    expect(within(result).getByText('What validation checks')).toBeInTheDocument();
    expect(within(result).getByText('It stays a custom skill')).toBeInTheDocument();
    expect(within(result).getByText('It assembles cleanly')).toBeInTheDocument();
    // Both recovery actions offered; no edited-effective section on a failure.
    expect(within(result).getByRole('link', { name: /View skill/i })).toBeInTheDocument();
    expect(within(result).getByRole('button', { name: /Re-validate/i })).toBeInTheDocument();
    expect(within(result).queryByText('Per-agent effect')).toBeNull();
  });

  test('Re-validate re-runs the guard and flips a failed edit to Validated', async () => {
    mount(CUSTOM_DETAIL, { editResponse: FAIL });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# fail');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    const result = await screen.findByLabelText('Validation result');
    await userEvent.click(within(result).getByRole('button', { name: /Re-validate/i }));
    await waitFor(() =>
      expect(screen.getByLabelText('Validation result')).toHaveAttribute(
        'data-result',
        'validated',
      ),
    );
  });

  test('copy discipline: the routed edit page uses no capability / approval / lifecycle language', async () => {
    mount(CUSTOM_DETAIL, { editResponse: FAIL });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# fail');
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    await screen.findByLabelText('Validation result');
    // The form AND a FAILED validation-result state are both mounted here. Scan
    // the FULL forbidden family (mirrors the Slice-2/3 tightened scans) plus a
    // user-facing "active" rejection (spec §3.4 / §9.1a — guidance visibility,
    // never permission / admit / materialize).
    const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(forbidden);
    expect(main).not.toMatch(/\bactive\b/i);
  });

  test('copy discipline: the PASS edited-effective state also stays clean', async () => {
    mount(CUSTOM_DETAIL, { editResponse: PASS_BUMP });
    await screen.findByRole('heading', { name: /Edit a custom skill/i });
    await userEvent.click(screen.getByRole('button', { name: /Save & re-validate/i }));
    await screen.findByLabelText('Validation result');
    const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(forbidden);
    expect(main).not.toMatch(/\bactive\b/i);
  });
});
