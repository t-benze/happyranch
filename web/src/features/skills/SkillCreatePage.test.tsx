import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

interface CreateResponse {
  skill_id: string;
  source: string;
  validation_state: 'in_catalog' | 'validated' | 'failed_validation';
  validation: { ok: boolean; errors: string[] };
}

const PASS: CreateResponse = {
  skill_id: 'hr:incident-postmortem',
  source: 'user_authored',
  validation_state: 'validated',
  validation: { ok: true, errors: [] },
};

const FAIL: CreateResponse = {
  skill_id: 'hr:incident-postmortem',
  source: 'user_authored',
  validation_state: 'in_catalog',
  validation: {
    ok: false,
    errors: [
      "slug collides with release skill 'jobs'",
      'The references/pricing.md asset could not be resolved.',
    ],
  },
};

function mount(createResponse: CreateResponse, status = 201) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.post(`/api/v1/orgs/${SLUG}/skills`, () =>
      HttpResponse.json(createResponse, { status }),
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
    route: `/orgs/${SLUG}/skills/new`,
  });
}

async function fillMinimalForm() {
  await userEvent.type(screen.getByLabelText(/Slug \/ id/i), 'incident-postmortem');
  await userEvent.type(screen.getByLabelText(/^Name/i), 'Incident postmortem');
  await userEvent.type(screen.getByLabelText(/SKILL\.md/i), '# Incident\n\n## When to use\nAfter an incident.');
}

describe('SkillCreatePage — add custom skill (THR-092 Slice 3)', () => {
  test('renders the form with required fields; no policy_class / system-contract control', async () => {
    mount(PASS);
    expect(await screen.findByRole('heading', { name: /Add a custom skill/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Slug \/ id/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/SKILL\.md/i)).toBeInTheDocument();
    // A custom skill can NEVER mint a system_contract — the UI must offer no
    // policy-class / system-contract control anywhere.
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/policy.?class/i);
    expect(main).not.toMatch(/system.?contract/i);
  });

  test('required-field guard blocks submit and lists what is missing', async () => {
    mount(PASS);
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    expect(await screen.findByText('Add a slug / id.')).toBeInTheDocument();
    expect(screen.getByText('Add a name.')).toBeInTheDocument();
    expect(screen.getByText(/Add the SKILL\.md guidance body/i)).toBeInTheDocument();
    // No result section without a round-trip.
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('SUCCESS path: renders Validated badge, confirmation, and a View skill link to the detail route', async () => {
    mount(PASS);
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'validated');
    expect(within(result).getByText('Validated')).toBeInTheDocument();
    expect(within(result).getByText(/technical checks passed/i)).toBeInTheDocument();
    const view = within(result).getByRole('link', { name: /View skill/i });
    expect(view).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/${encodeURIComponent('hr:incident-postmortem')}`,
    );
    // Success does not offer Re-validate.
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('FAILURE path: maps issues to plain language + explains every validation check + offers View skill / Re-validate', async () => {
    mount(FAIL);
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'failed_validation');
    // Badge is product language, not raw state.
    expect(within(result).getByText('Needs attention')).toBeInTheDocument();
    // Failure framed as a fixable technical check — not a dead end.
    expect(within(result).getByText(/not a review gate/i)).toBeInTheDocument();
    // Raw backend errors mapped to plain-language, actionable guidance.
    expect(within(result).getByText(/already used by a bundled skill/i)).toBeInTheDocument();
    expect(within(result).getByText(/referenced file couldn’t be found/i)).toBeInTheDocument();
    // The plain-language explanation covers EVERY check.
    expect(within(result).getByText('What validation checks')).toBeInTheDocument();
    expect(within(result).getByText('The package reads cleanly')).toBeInTheDocument();
    expect(within(result).getByText('It stays a custom skill')).toBeInTheDocument();
    expect(within(result).getByText('It assembles cleanly')).toBeInTheDocument();
    // The draft is still persisted → both recovery actions are offered.
    expect(within(result).getByRole('link', { name: /View skill/i })).toHaveAttribute(
      'href',
      `/orgs/${SLUG}/skills/${encodeURIComponent('hr:incident-postmortem')}`,
    );
    expect(within(result).getByRole('button', { name: /Re-validate/i })).toBeInTheDocument();
  });

  test('Re-validate re-runs the guard and flips the result to Validated', async () => {
    mount(FAIL);
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    const result = await screen.findByLabelText('Validation result');
    await userEvent.click(within(result).getByRole('button', { name: /Re-validate/i }));
    await waitFor(() =>
      expect(screen.getByLabelText('Validation result')).toHaveAttribute(
        'data-result',
        'validated',
      ),
    );
    expect(screen.getByText(/technical checks passed/i)).toBeInTheDocument();
  });

  test('copy discipline: the add/validation surface uses no capability/approval UI language', async () => {
    mount(FAIL);
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    await screen.findByLabelText('Validation result');
    // Scope to the routed content column. The guidance strip legitimately
    // NEGATES permission wording ("never grant ... permissions"), so — like
    // SkillsPage.test — scan only for the capability/approval UI language that
    // must never appear, not the negated words.
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/\bactive\b/i);
    expect(main).not.toMatch(/\bapprove\b/i);
    expect(main).not.toMatch(/\badmit\b/i);
    expect(main).not.toMatch(/\bpending\b/i);
    expect(main).not.toMatch(/materializ/i);
  });
});
