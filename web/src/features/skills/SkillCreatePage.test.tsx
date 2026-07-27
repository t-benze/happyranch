import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';

// Lifecycle-native response shape from POST /skill-lifecycle/proposals.
interface ProposalResponse {
  skill_id: string;
  version_id: number;
  version: string;
  status: string;
  content_hash: string;
  content_artifact_key: string | null;
  proposal_task_id: string | null;
}

const PASS_PROPOSAL: ProposalResponse = {
  skill_id: 'hr:incident-postmortem',
  version_id: 42,
  version: '0.1.0',
  status: 'proposed',
  content_hash: 'abc123def456',
  content_artifact_key: 'skill-lifecycle/incident-postmortem/abc123/SKILL.md',
  proposal_task_id: null,
};

/** Mount the create page with lifecycle endpoint mocks. */
function mount(opts: {
  proposalResponse?: ProposalResponse;
  proposalStatus?: number;
  lifecycleStatusResponse?: Record<string, unknown>;
} = {}) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    // Lifecycle proposal endpoint (replaces legacy POST /skills).
    http.post(`/api/v1/orgs/${SLUG}/skill-lifecycle/proposals`, () =>
      HttpResponse.json(
        opts.proposalResponse ?? PASS_PROPOSAL,
        { status: opts.proposalStatus ?? 201 },
      ),
    ),
    // Lifecycle status read (used by Re-validate).
    http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/:skillId`, ({ params }) =>
      HttpResponse.json(
        opts.lifecycleStatusResponse ?? {
          skill_id: params.skillId,
          slug: 'incident-postmortem',
          current_status: 'proposed',
          current_version: '0.1.0',
          current_version_id: 42,
          published_version: null,
          assignments: [],
          events: [],
          proposal_task_id: null,
          proposer_agent: null,
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
    mount();
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
    mount();
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    expect(await screen.findByText('Add a slug / id.')).toBeInTheDocument();
    expect(screen.getByText('Add a name.')).toBeInTheDocument();
    expect(screen.getByText(/Add the SKILL\.md guidance body/i)).toBeInTheDocument();
    // No result section without a round-trip.
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('SUCCESS path: renders Proposed badge, confirmation, and NO catalog detail link (proposed stays outside catalog)', async () => {
    mount();
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));

    const result = await screen.findByLabelText('Validation result');
    expect(result).toHaveAttribute('data-result', 'proposed');
    expect(within(result).getByText('Proposed')).toBeInTheDocument();
    expect(within(result).getByText(/awaiting review/i)).toBeInTheDocument();
    // Proposed skills are NOT in the catalog — no View skill / catalog-detail
    // link must be rendered (TASK-3488).
    expect(within(result).queryByRole('link', { name: /View skill/i })).toBeNull();
    // Proposed state does NOT expose a catalog claim.
    expect(within(result).queryByText(/catalog/i)).toBeNull();
    // Proposed state does not offer Re-validate.
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('FAILURE path: proposal error shows submit-error message, no validation result section', async () => {
    // Lifecycle-native: a failed proposal (e.g., slug collision → 409) is caught
    // by the component's error handler and shown as a submitError — NOT as a
    // "validation result" section.
    mount({ proposalStatus: 409 });
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));

    // The submit-error banner is rendered.
    expect(
      await screen.findByText(/Could not save the skill/i),
    ).toBeInTheDocument();
    // No validation result section appears — the proposal never resolved.
    expect(screen.queryByLabelText('Validation result')).toBeNull();
  });

  test('SUCCESS path does not offer Re-validate (lifecycle-native: proposals are accepted, not iteratively validated)', async () => {
    mount();
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    const result = await screen.findByLabelText('Validation result');
    // Proposed state never offers Re-validate — proposal acceptance is terminal
    // from the agent's perspective (human review comes later). Scoped to result
    // section so the submit button "Validate & save" isn't matched.
    expect(within(result).queryByRole('button', { name: /Re-validate/i })).toBeNull();
  });

  test('copy discipline: the add/validation surface uses no capability/approval UI language', async () => {
    mount();
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    await screen.findByLabelText('Validation result');
    // Guidance-visibility copy carries NO capability / approval / lifecycle
    // language anywhere in the routed create-page content — the form AND the
    // validation-result state rendered below it.
    const forbidden = /materializ|admit|permission|approve|grant|\bpending\b/i;
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(forbidden);
    expect(main).not.toMatch(/\bactive\b/i);
  });

  test('proposed page contains NO catalog or editable-draft claim (entire page, not only result section)', async () => {
    mount();
    await screen.findByRole('heading', { name: /Add a custom skill/i });
    await fillMinimalForm();
    await userEvent.click(screen.getByRole('button', { name: /Validate & save/i }));
    await screen.findByLabelText('Validation result');
    // The ENTIRE rendered page must contain no catalog claim or editable-draft
    // language — the form footer still renders after submission, so a result-
    // scoped assertion misses residual copy (TASK-3491 finding 1).
    const main = document.querySelector('main')?.textContent ?? '';
    expect(main).not.toMatch(/in the catalog/i);
    expect(main).not.toMatch(/editable draft/i);
    expect(main).not.toMatch(/failed check/i);
  });
});
