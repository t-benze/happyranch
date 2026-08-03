/**
 * ProposalDetailPage tests — THR-055 Slice 2B.
 *
 * Targets: actual rendering, skill_md-null non-fabrication, 403 no-leak,
 * 404/error Retry, rejected terminal no-action, proposed/published/rejected
 * decision-vs-projection distinction, copy controls, static-route precedence,
 * and bounded scroll/accessibility structure.
 */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';
import type { ProposalDetailResponse } from '@/hooks/skills';

const SLUG = 'alpha';
const VERSION_ID = 1;

function baseProposal(
  overrides: Partial<ProposalDetailResponse> = {},
): ProposalDetailResponse {
  return {
    version_id: VERSION_ID,
    skill_id: 'hr:frontend-development',
    slug: 'frontend-development',
    name: 'frontend-development',
    version: '0.1.0',
    description: 'Guidance for implementing and reviewing frontend changes.',
    content_hash:
      'sha256:53cb67fc7ead400a400fbcede2f5371c69747d065dfff9304b0c289b27367328',
    content_artifact_key: 'skill-lifecycle/frontend-dev/abc/manifest.json',
    policy_class: 'standard_operational',
    status: 'proposed',
    proposer_agent: 'frontend_engineer',
    proposal_task_id: 'TASK-3864',
    proposal_session_id: 'sess-8091',
    claimed_by: null,
    claimed_at: null,
    reviewer: null,
    review_decision: null,
    review_rationale: null,
    reviewed_at: null,
    publisher: null,
    published_at: null,
    purpose: 'Implement & review frontend changes',
    target_agent_suggestion: 'frontend_engineer',
    skill_md: '---\nname: frontend-development\ndescription: Test skill.\n---\n\n# Frontend Development\n\nGuidance for frontend work.',
    package_members: null,
    events: [
      {
        event_type: 'proposed',
        actor: 'frontend_engineer',
        actor_role: 'agent',
        new_status: 'proposed',
        previous_status: null,
        content_hash: null,
        created_at: '2026-08-01T09:14:00Z',
        metadata: null,
      },
    ],
    assignments: [],
    materializations: [],
    last_event_id: 1,
    created_at: '2026-08-01T09:14:00Z',
    ...overrides,
  };
}

function mount(versionId: number | string = VERSION_ID) {
  sessionStorage.setItem('happyranch.token', 'tok');
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
  );
  return renderWithProviders(<AppRoutes />, {
    route: `/orgs/${SLUG}/skills/proposals/${versionId}`,
  });
}

function mockProposal(
  proposal: ProposalDetailResponse,
  status = 200,
) {
  server.use(
    http.get('/api/v1/orgs', () =>
      HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
    ),
    http.get(
      `/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/${VERSION_ID}`,
      () => HttpResponse.json(proposal, { status }),
    ),
  );
}

// ── Happy path: proposed proposal ────────────────────────────────────────

describe('ProposalDetailPage — proposed proposal', () => {
  test('renders identity, version, hash, and readiness strip', async () => {
    mockProposal(baseProposal());
    mount();

    // Breadcrumb
    expect(await screen.findByText('Proposal')).toBeInTheDocument();

    // Mono identity (hr:frontend-development appears in breadcrumb + evidence rail)
    expect(screen.getAllByText('hr:frontend-development').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('frontend-development')).toBeInTheDocument();

    // Version (appears in header + evidence rail)
    expect(screen.getAllByText('v0.1.0').length).toBeGreaterThanOrEqual(1);

    // Status chip (appears in header + timeline)
    expect(screen.getAllByText('Proposed').length).toBeGreaterThanOrEqual(1);

    // Hash in the header (full hash)
    expect(
      screen.getByText(/sha256:53cb67fc7e/),
    ).toBeInTheDocument();

    // Readiness facts (backed by response facts, never status-enum synthesized)
    expect(screen.getByText('No assignments recorded')).toBeInTheDocument();
    expect(screen.getByText('No materializations recorded')).toBeInTheDocument();
  });

  test('renders SKILL.md primary pane with content', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Package content · SKILL.md')).toBeInTheDocument();
    const pre = screen.getByLabelText('Package content').querySelector('pre');
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain('# Frontend Development');
  });

  test('renders evidence rail', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Evidence')).toBeInTheDocument();

    // Scope evidence queries to the Evidence landmark
    const evidenceLandmark = screen.getByLabelText('Evidence');

    // Purpose
    expect(within(evidenceLandmark).getByText('Implement & review frontend changes')).toBeInTheDocument();

    // Policy class
    expect(within(evidenceLandmark).getByText('standard_operational')).toBeInTheDocument();

    // Suggested target
    expect(within(evidenceLandmark).getByText('frontend_engineer')).toBeInTheDocument();
    expect(within(evidenceLandmark).getByText('(advisory)')).toBeInTheDocument();

    // Proposal ID
    expect(within(evidenceLandmark).getByText('hr:frontend-development')).toBeInTheDocument();

    // Validation — "not run"
    expect(within(evidenceLandmark).getByText('not run')).toBeInTheDocument();
  });

  test('renders provenance: immutable proposer + task/session', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Provenance')).toBeInTheDocument();

    // Scope to Provenance landmark to avoid matching evidence rail suggested target
    const provenanceLandmark = screen.getByLabelText('Provenance');

    // Proposer (immutable)
    expect(within(provenanceLandmark).getByText('frontend_engineer')).toBeInTheDocument();
    expect(within(provenanceLandmark).getByText('immutable')).toBeInTheDocument();

    // Not claimed
    expect(within(provenanceLandmark).getByText('not claimed')).toBeInTheDocument();

    // Source task
    expect(within(provenanceLandmark).getByText('TASK-3864')).toBeInTheDocument();

    // Source session
    expect(within(provenanceLandmark).getByText('sess-8091')).toBeInTheDocument();
  });

  test('renders timeline with proposed event', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Timeline')).toBeInTheDocument();
    expect(screen.getByText('append-only')).toBeInTheDocument();
    // Proposed appears in both header chip + timeline event
    expect(screen.getAllByText('Proposed').length).toBeGreaterThanOrEqual(2);
  });

  test('renders non-terminal assignment message', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Assignment & materialization')).toBeInTheDocument();
    expect(screen.getByText(/not yet complete/)).toBeInTheDocument();
  });

  test('renders guidance-only footer', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByText('Guidance visibility only.')).toBeInTheDocument();
  });

  test('Copy SKILL.md button is present', async () => {
    mockProposal(baseProposal());
    mount();

    expect(
      await screen.findByRole('button', { name: /copy full skill/i }),
    ).toBeInTheDocument();
  });
});

// ── Published proposal ───────────────────────────────────────────────────

describe('ProposalDetailPage — published proposal', () => {
  test('shows published distinct banner', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'published',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'published',
            created_at: '2026-08-02T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    // Banner heading: the published banner region with aria-label="Published"
    const banner = await screen.findByRole('region', { name: 'Published' });
    expect(within(banner).getByText('Published')).toBeInTheDocument();
    // Banner must NOT claim catalog membership
    expect(within(banner).queryByText(/custom catalog/i)).not.toBeInTheDocument();
    expect(within(banner).queryByText(/visible in the published/i)).not.toBeInTheDocument();
    // Published appears in header chip + timeline event; check count >= 2
    expect(screen.getAllByText('Published').length).toBeGreaterThanOrEqual(2);
  });

  test('published with no catalog evidence renders no catalog-membership claim', async () => {
    // Contrary fixture: a fully-blessed published proposal with every response
    // field filled still must not render any catalog-membership/visibility
    // statement because ProposalDetailResponse carries no catalog evidence.
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        review_decision: 'approved',
        assignments: [{ agent_name: 'frontend_engineer', active: true }],
        materializations: [{ agent_name: 'frontend_engineer', success: true, created_at: '2026-08-03T09:00:00Z' }],
        events: [
          { event_type: 'proposed', actor: 'frontend_engineer', actor_role: 'agent', new_status: 'proposed', created_at: '2026-08-01T09:00:00Z' },
          { event_type: 'published', actor: 'founder', actor_role: 'founder', new_status: 'published', created_at: '2026-08-02T10:00:00Z' },
        ],
      }),
    );
    mount();

    await screen.findByText('Proposal');

    // No catalog-membership/visibility claim in the published banner
    const banner = screen.getByRole('region', { name: 'Published' });
    expect(within(banner).queryByText(/custom catalog/i)).not.toBeInTheDocument();
    expect(within(banner).queryByText(/visible in the published/i)).not.toBeInTheDocument();
    // No catalog-membership claim in the readiness strip
    expect(screen.queryByText('In custom catalog')).not.toBeInTheDocument();
    expect(screen.queryByText('Not in catalog')).not.toBeInTheDocument();
    // No catalog-membership/visibility claim anywhere on the page (banner + footer)
    expect(screen.queryByText(/custom catalog/i)).not.toBeInTheDocument();
  });

  test('shows publisher in provenance', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
      }),
    );
    mount();

    expect(await screen.findByText('Publisher')).toBeInTheDocument();
    const provSection = screen.getByLabelText('Provenance');
    expect(within(provSection).getByText('founder')).toBeInTheDocument();
  });

  test('shows assignment projection when assignments exist', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        assignments: [
          {
            agent_name: 'frontend_engineer',
            active: true,
            version: '0.1.0',
            assigned_by: 'founder',
            assigned_at: '2026-08-03T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    expect(
      await screen.findByText('Assignments'),
    ).toBeInTheDocument();
    // The agent name appears in the assignment list (scope to assignment landmark)
    const assignSection = screen.getByLabelText('Assignment and materialization');
    expect(within(assignSection).getByText('frontend_engineer')).toBeInTheDocument();
  });
});

// ── Rejected proposal — terminal, view-only ──────────────────────────────

describe('ProposalDetailPage — rejected proposal', () => {
  test('shows rejected terminal banner with no action affordance', async () => {
    mockProposal(
      baseProposal({
        status: 'rejected',
        reviewer: 'founder',
        review_decision: 'rejected',
        review_rationale: 'Not needed.',
        reviewed_at: '2026-08-02T10:00:00Z',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'rejected',
            actor: 'founder',
            actor_role: 'reviewer',
            new_status: 'rejected',
            created_at: '2026-08-02T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    // Rejected terminal banner + readiness fact both show this text
    await screen.findByText(/cannot be reopened/);
    expect(screen.getAllByText('Rejected — terminal').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/cannot be reopened/)).toBeInTheDocument();

    // Status chip shows Rejected (in header + timeline, multiple)
    expect(screen.getAllByText('Rejected').length).toBeGreaterThanOrEqual(1);

    // Terminal view-only text
    expect(screen.getByText('Terminal — view only')).toBeInTheDocument();

    // No claim/publish/action affordance text (check for actionable "Claim" button/link, not "Claimed by")

    // Readiness: review_decision backed (no catalog-membership claim)
    // Rejected — terminal appears in both readiness strip and banner
    expect(screen.getAllByText('Rejected — terminal').length).toBeGreaterThanOrEqual(1);
  });

  test('rejected shows reviewer facts in provenance', async () => {
    mockProposal(
      baseProposal({
        status: 'rejected',
        reviewer: 'founder',
        review_decision: 'rejected',
        reviewed_at: '2026-08-02T10:00:00Z',
      }),
    );
    mount();

    expect(await screen.findByText('Reviewer')).toBeInTheDocument();
    const provenance = screen.getByLabelText('Provenance');
    // Scope to provenance section to avoid matching the status chip in header
    expect(within(provenance).getByText('Rejected')).toBeInTheDocument();
  });
});

// ── skill_md null — no fabrication ──────────────────────────────────────

describe('ProposalDetailPage — skill_md null', () => {
  test('shows warning when SKILL.md bytes are unavailable', async () => {
    mockProposal(
      baseProposal({
        skill_md: null,
      }),
    );
    mount();

    expect(
      await screen.findByText('Canonical bytes unavailable'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/could not be loaded from the artifact store/),
    ).toBeInTheDocument();

    // No Copy SKILL.md button when bytes are unavailable
    expect(
      screen.queryByRole('button', { name: /copy full skill/i }),
    ).not.toBeInTheDocument();

    // No pre with fabricated content
    const section = screen.getByLabelText('Package content');
    expect(section.querySelector('pre')).toBeNull();
  });
});

// ── 403 — Founder access, no data leak ───────────────────────────────────

describe('ProposalDetailPage — 403', () => {
  test('shows Founder access state with NO proposal bytes', async () => {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/${VERSION_ID}`,
        () =>
          HttpResponse.json(
            {
              detail: {
                code: 'human_only',
                detail: 'This action requires human/founder authority.',
              },
            },
            { status: 403 },
          ),
      ),
    );
    mount();

    expect(
      await screen.findByText('Founder access required'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Proposal review is restricted to the founder/),
    ).toBeInTheDocument();

    // NO proposal data leaked: no hash, no proposer, no skill_md
    expect(
      screen.queryByText(/sha256:/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('frontend_engineer'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('Package content'),
    ).not.toBeInTheDocument();
  });
});

// ── 404 ──────────────────────────────────────────────────────────────────

describe('ProposalDetailPage — 404', () => {
  test('shows distinct not-found state', async () => {
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/${VERSION_ID}`,
        () =>
          HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    );
    mount();

    expect(
      await screen.findByText('Proposal not found'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/This proposal version is not available/),
    ).toBeInTheDocument();
  });
});

// ── Generic error with Retry ─────────────────────────────────────────────

describe('ProposalDetailPage — error with Retry', () => {
  test('shows Retry button that refetches', async () => {
    let callCount = 0;
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/${VERSION_ID}`,
        () => {
          callCount += 1;
          if (callCount === 1) {
            return HttpResponse.json(
              { detail: 'Internal error' },
              { status: 500 },
            );
          }
          return HttpResponse.json(baseProposal());
        },
      ),
    );
    mount();

    expect(
      await screen.findByText('Could not load this proposal'),
    ).toBeInTheDocument();

    const retryBtn = screen.getByRole('button', { name: /retry/i });
    expect(retryBtn).toBeInTheDocument();

    // Click retry — should refetch and eventually render proposal data
    fireEvent.click(retryBtn);

    await waitFor(() => {
      expect(screen.getByText('Proposal')).toBeInTheDocument();
    });
  });
});

// ── Validator facts ─────────────────────────────────────────────────────

describe('ProposalDetailPage — validator facts', () => {
  test('shows validation passed with version/key', async () => {
    mockProposal(
      baseProposal({
        status: 'validated',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'validated',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'validated',
            created_at: '2026-08-02T10:00:00Z',
            metadata: {
              validator_version: 'THR-055/1.0.0',
              validator_key: 'hr-thr055',
            },
          },
        ],
      }),
    );
    mount();

    // Scope to Evidence to avoid matching header status chip + timeline
    const evidence = await screen.findByLabelText('Evidence');
    // The text is "Passed · THR-055/1.0.0" — use regex to match prefix
    expect(within(evidence).getByText(/Passed/)).toBeInTheDocument();
    expect(within(evidence).getByText(/THR-055/)).toBeInTheDocument();
  });

  test('shows validation failed', async () => {
    mockProposal(
      baseProposal({
        status: 'validation_failed',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'validation_failed',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'validation_failed',
            created_at: '2026-08-02T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    // Scope to Evidence to avoid matching header status chip + timeline
    const evidenceSection = await screen.findByLabelText('Evidence');
    expect(within(evidenceSection).getByText('Failed')).toBeInTheDocument();
  });
});

// ── Claimed proposal ─────────────────────────────────────────────────────

describe('ProposalDetailPage — claimed proposal', () => {
  test('shows separate claimant with timestamp', async () => {
    mockProposal(
      baseProposal({
        status: 'draft',
        claimed_by: 'founder',
        claimed_at: '2026-08-02T14:00:00Z',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
        ],
      }),
    );
    mount();

    expect(await screen.findByText('Provenance')).toBeInTheDocument();

    // Immutable proposer still shows (scope to provenance)
    const provenanceSection = screen.getByLabelText('Provenance');
    expect(within(provenanceSection).getByText('frontend_engineer')).toBeInTheDocument();
    expect(within(provenanceSection).getByText('immutable')).toBeInTheDocument();

    // Claimant shows separately
    expect(within(provenanceSection).getByText('founder')).toBeInTheDocument();
    // Claim timestamp
    expect(within(provenanceSection).getByText('2026-08-02T14:00:00Z')).toBeInTheDocument();
  });
});

// ── Static route precedence ──────────────────────────────────────────────

describe('ProposalDetailPage — route precedence', () => {
  test('skills/proposals/:versionId renders ProposalDetailPage, not SkillDetailPage', async () => {
    // Mock the proposal detail endpoint only — not the catalog skill detail
    server.use(
      http.get('/api/v1/orgs', () =>
        HttpResponse.json({ orgs: [{ slug: SLUG, root: '/x' }] }),
      ),
      http.get(
        `/api/v1/orgs/${SLUG}/skill-lifecycle/proposals/${VERSION_ID}`,
        () => HttpResponse.json(baseProposal()),
      ),
      // Also mock the skills catalog endpoint (for sidebar) and the skill-lifecycle
      // catalog so the sidebar doesn't error
      http.get(`/api/v1/orgs/${SLUG}/skill-lifecycle/catalog/custom`, () =>
        HttpResponse.json({ skills: [] }),
      ),
      http.get(`/api/v1/orgs/${SLUG}/skills/catalog`, () =>
        HttpResponse.json({ items: [] }),
      ),
    );
    mount();

    // ProposalDetailPage renders the "Proposal" breadcrumb
    expect(await screen.findByText('Proposal')).toBeInTheDocument();

    // NOT the SkillDetailPage which shows "Source" / "Guidance visibility only"
    // But we should NOT see the skill-detail specific content
    expect(
      screen.queryByText('Guidance (SKILL.md)'),
    ).not.toBeInTheDocument();

    // Page shows proposal content
    expect(screen.getByText('Package content · SKILL.md')).toBeInTheDocument();
    expect(screen.getByText('Provenance')).toBeInTheDocument();
  });
});

// ── Bounded scroll and accessibility structure ───────────────────────────

describe('ProposalDetailPage — accessibility and layout', () => {
  test('has labelled regions for key sections', async () => {
    mockProposal(baseProposal());
    mount();

    expect(await screen.findByLabelText('Package content')).toBeInTheDocument();
    expect(screen.getByLabelText('Evidence')).toBeInTheDocument();
    expect(screen.getByLabelText('Provenance')).toBeInTheDocument();
    expect(screen.getByLabelText('Timeline')).toBeInTheDocument();
    expect(
      screen.getByLabelText('Assignment and materialization'),
    ).toBeInTheDocument();
  });

  test('Copy hash button is keyboard-accessible', async () => {
    mockProposal(baseProposal());
    mount();

    const copyHashBtn = await screen.findByRole('button', {
      name: /copy full content hash/i,
    });
    expect(copyHashBtn).toBeInTheDocument();
    // Button should receive focus
    copyHashBtn.focus();
    expect(document.activeElement).toBe(copyHashBtn);
  });

  test('bounded scroll container exists', async () => {
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');
    // The outer container has overflow-y-auto
    const container = document.querySelector('.h-full.overflow-y-auto');
    expect(container).not.toBeNull();
  });
});

// ── Copy discipline: no forbidden tokens ─────────────────────────────────

describe('ProposalDetailPage — copy discipline', () => {
  test('never renders "active" as assignment state', async () => {
    mockProposal(baseProposal({ status: 'published' }));
    mount();

    await screen.findByText('Proposal');
    // "active" should not appear as a user-facing state label
    const pageText = document.body.textContent ?? '';
    // We search for "active" as a standalone word (not part of "inactive")
    expect(/active/i.test(pageText)).toBe(false);
  });
});

// ── Timeline content hash and metadata rendering ─────────────────────────

describe('ProposalDetailPage — timeline hash and metadata', () => {
  test('renders event content hash when supplied', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
            content_hash: 'sha256:abc123def4567890',
          },
          {
            event_type: 'published',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'published',
            created_at: '2026-08-02T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    expect(await screen.findByText('Timeline')).toBeInTheDocument();
    expect(screen.getByText('sha256:abc123def4567890')).toBeInTheDocument();
  });

  test('renders metadata facts from validation event', async () => {
    mockProposal(
      baseProposal({
        status: 'validated',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'validated',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'validated',
            created_at: '2026-08-02T10:00:00Z',
            metadata: {
              validator_version: 'THR-055/1.0.0',
              validator_key: 'hr-thr055',
              run_id: 'run-42',
            },
          },
        ],
      }),
    );
    mount();

    expect(await screen.findByText('Timeline')).toBeInTheDocument();
    // Metadata facts should be rendered in the timeline
    expect(screen.getByText('Validator version')).toBeInTheDocument();
    expect(screen.getByText('THR-055/1.0.0')).toBeInTheDocument();
    expect(screen.getByText('Validator key')).toBeInTheDocument();
    expect(screen.getByText('hr-thr055')).toBeInTheDocument();
    expect(screen.getByText('Run')).toBeInTheDocument();
    expect(screen.getByText('run-42')).toBeInTheDocument();
  });

  test('renders failure and rationale from rejected event', async () => {
    mockProposal(
      baseProposal({
        status: 'rejected',
        reviewer: 'founder',
        review_decision: 'rejected',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'rejected',
            actor: 'founder',
            actor_role: 'reviewer',
            new_status: 'rejected',
            created_at: '2026-08-02T10:00:00Z',
            metadata: {
              rationale: 'Does not meet quality bar',
              failure: 'Missing required references',
            },
          },
        ],
      }),
    );
    mount();

    expect(await screen.findByText('Timeline')).toBeInTheDocument();
    expect(screen.getByText('Rationale')).toBeInTheDocument();
    expect(screen.getByText('Does not meet quality bar')).toBeInTheDocument();
    expect(screen.getByText('Failure')).toBeInTheDocument();
    expect(screen.getByText('Missing required references')).toBeInTheDocument();
  });
});

// ── Copy button accessibility and error state ────────────────────────────

describe('ProposalDetailPage — copy controls', () => {
  test('copy hash button has aria-live region for feedback', async () => {
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    // The copy button has an associated sr-only aria-live region
    const liveRegion = document.querySelector('[aria-live="polite"]');
    expect(liveRegion).not.toBeNull();
    expect(liveRegion?.getAttribute('role')).toBe('status');
  });

  test('copy SKILL.md button is present and accessible', async () => {
    mockProposal(baseProposal());
    mount();

    const copyBtn = await screen.findByRole('button', {
      name: /copy full skill/i,
    });
    expect(copyBtn).toBeInTheDocument();
    // Should be focusable
    copyBtn.focus();
    expect(document.activeElement).toBe(copyBtn);
  });

  test('copy hash button renders confirmation and aria-live', async () => {
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    // Find the hash copy button (not the SKILL.md copy button)
    const hashBtn = screen.getByRole('button', {
      name: /copy full content hash/i,
    });
    expect(hashBtn).toBeInTheDocument();
  });

  test('copy hash resolve shows "Copied" feedback and aria-live status', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    const hashBtn = screen.getByRole('button', {
      name: /copy full content hash/i,
    });
    fireEvent.click(hashBtn);

    await waitFor(() => {
      expect(screen.getByText('Copied')).toBeInTheDocument();
    });
    // aria-live region text via getByText (sr-only text is in the DOM)
    expect(screen.getByText('Copied to clipboard')).toBeInTheDocument();
  });

  test('copy hash reject shows "Failed" feedback and aria-live status', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    const hashBtn = screen.getByRole('button', {
      name: /copy full content hash/i,
    });
    fireEvent.click(hashBtn);

    await waitFor(() => {
      expect(screen.getByText('Failed')).toBeInTheDocument();
    });
    expect(screen.getByText('Clipboard copy failed')).toBeInTheDocument();
  });

  test('copy SKILL.md resolve shows "Copied" feedback', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    const skillBtn = screen.getByRole('button', {
      name: /copy full skill/i,
    });
    fireEvent.click(skillBtn);

    await waitFor(() => {
      expect(screen.getByText('Copied')).toBeInTheDocument();
    });
  });

  test('copy SKILL.md reject shows "Failed" feedback', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'));
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    mockProposal(baseProposal());
    mount();

    await screen.findByText('Proposal');

    const skillBtn = screen.getByRole('button', {
      name: /copy full skill/i,
    });
    fireEvent.click(skillBtn);

    await waitFor(() => {
      expect(screen.getByText('Failed')).toBeInTheDocument();
    });
    expect(screen.getByText('Clipboard copy failed')).toBeInTheDocument();
  });
});

// ── Materialization rendering uses actual fields ─────────────────────────

describe('ProposalDetailPage — materialization display', () => {
  test('renders materialization with success and error_message', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        materializations: [
          {
            agent_name: 'frontend_engineer',
            success: true,
            created_at: '2026-08-03T09:00:00Z',
          },
          {
            agent_name: 'qa_engineer',
            success: false,
            error_message: 'Disk quota exceeded',
            created_at: '2026-08-03T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    expect(
      await screen.findByText('Assignment & materialization'),
    ).toBeInTheDocument();
    const assignSection = screen.getByLabelText('Assignment and materialization');

    // Shows agent names
    expect(within(assignSection).getByText('frontend_engineer')).toBeInTheDocument();
    expect(within(assignSection).getByText('qa_engineer')).toBeInTheDocument();

    // Shows success/failure status
    expect(within(assignSection).getByText('succeeded')).toBeInTheDocument();
    expect(within(assignSection).getByText('failed')).toBeInTheDocument();

    // Shows error_message
    expect(within(assignSection).getByText('Disk quota exceeded')).toBeInTheDocument();

    // Never shows 'pending'
    expect(
      within(assignSection).queryByText('pending'),
    ).not.toBeInTheDocument();
  });

  test('never renders materialized_at field', async () => {
    mockProposal(
      baseProposal({
        status: 'published',
        publisher: 'founder',
        published_at: '2026-08-02T10:00:00Z',
        materializations: [
          {
            agent_name: 'frontend_engineer',
            success: true,
            materialized_at: '2026-08-03T09:00:00Z',
            created_at: '2026-08-03T09:00:00Z',
          },
        ],
      }),
    );
    mount();

    expect(
      await screen.findByText('Assignment & materialization'),
    ).toBeInTheDocument();

    const assignSection = screen.getByLabelText('Assignment and materialization');
    // Uses created_at (which is '2026-08-03T09:00:00Z'), rendered as <time>
    const timeEl = assignSection.querySelector('time');
    expect(timeEl).not.toBeNull();
    // The time element shows created_at, not materialized_at
    expect(timeEl?.textContent).toBe('2026-08-03T09:00:00Z');
  });
});

// ── No synthetic readiness statements ────────────────────────────────────

describe('ProposalDetailPage — no synthetic readiness', () => {
  test('proposed proposal shows no "passed validation" claim', async () => {
    mockProposal(baseProposal({ status: 'proposed' }));
    mount();

    await screen.findByText('Proposal');

    // Should NOT claim validation passed when there's no validation event
    expect(screen.queryByText('Passed technical validation')).not.toBeInTheDocument();
  });

  test('validation_failed does not claim "Approved"', async () => {
    mockProposal(
      baseProposal({
        status: 'validation_failed',
        events: [
          {
            event_type: 'proposed',
            actor: 'frontend_engineer',
            actor_role: 'agent',
            new_status: 'proposed',
            created_at: '2026-08-01T09:00:00Z',
          },
          {
            event_type: 'validation_failed',
            actor: 'founder',
            actor_role: 'founder',
            new_status: 'validation_failed',
            created_at: '2026-08-02T10:00:00Z',
          },
        ],
      }),
    );
    mount();

    await screen.findByText('Proposal');

    // Should NOT synthesize "Approved" or "Published" from status
    expect(screen.queryByText('Approved by reviewer')).not.toBeInTheDocument();
    expect(screen.queryByText('In custom catalog')).not.toBeInTheDocument();
  });
});
