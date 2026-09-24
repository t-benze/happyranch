import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest';
import {
  activateTeamEscalationPolicyRelease,
  activateTeamEscalationPolicyV2,
  createAndActivateTeamEscalationPolicyV2,
  decodeTeamEscalationPolicyResponse,
  getTeamEscalationPolicy,
  type TeamEscalationPolicyResponse,
} from './authorityPolicy';

vi.mock('./client', () => ({ request: vi.fn() }));
import { request } from './client';

const template = {
  title: 'Policy', normative_text: 'Text', clauses: [],
  continuation_phrase: 'server-authored phrase',
};
const v2Starter = {
  policy_id: 'team-8c85b6639e62e10b-dual-text',
  title: 'Engineering escalation policy',
  what_to_escalate: 'Escalate starter.',
  what_not_to_escalate: 'Continue starter.',
};
const SELECTOR_ID = `APS-${'c'.repeat(64)}`;
const RELEASE_DIGEST = 'd'.repeat(64);
const ACTIVATION_DIGEST = 'e'.repeat(64);

function v2ControlReceipt(kind: 'v2_create_activate' | 'v2_activate') {
  return {
    team: 'engineering', kind,
    create_request_id: kind === 'v2_create_activate' ? 'req-create-1' : null,
    create_request_digest: kind === 'v2_create_activate' ? 'a'.repeat(64) : null,
    activation_request_id: 'req-activate-1', activation_request_digest: 'b'.repeat(64),
    release_id: `APV2-${RELEASE_DIGEST}`, policy_digest: RELEASE_DIGEST, release_version: 1,
    activation_id: `APV2A-${ACTIVATION_DIGEST}`, activation_digest: ACTIVATION_DIGEST,
    selector_id: SELECTOR_ID, selector_epoch: 3, action: 'activate',
    previous_selector_id: SELECTOR_ID, created_at: '2026-09-03T00:00:00Z',
  } as const;
}
const empty = {
  team: 'engineering',
  target_manager: 'engineering_manager',
  can_mutate: true,
  family: 'empty',
  selector_id: SELECTOR_ID,
  selector_epoch: 0,
  bootstrap_required: true,
  bootstrap_template: template,
  v2_starter: v2Starter,
} as const;
const legacy = {
  team: 'engineering',
  target_manager: 'engineering_manager',
  can_mutate: true,
  family: 'legacy_v1',
  contract_version: 'v1',
  selector_id: SELECTOR_ID,
  selector_epoch: 1,
  bootstrap_template: template,
  v2_starter: v2Starter,
  active: {
    family: 'legacy_v1',
    activation_id: 'APA-1',
    epoch: 1,
    action: 'bootstrap',
    created_at: '2026-09-02T00:00:00Z',
    actor_attribution: 'shared local operator credential',
    release: {
      id: 'APR-1', policy_id: 'p', version: 1, ...template, digest: 'd',
      created_at: '2026-09-02T00:00:00Z',
      actor_attribution: 'shared local operator credential',
    },
  },
} as const;
const v2 = {
  team: 'engineering',
  target_manager: 'engineering_manager',
  can_mutate: true,
  family: 'v2',
  contract_version: 'v2',
  selector_id: SELECTOR_ID,
  selector_epoch: 2,
  bootstrap_template: template,
  v2_starter: v2Starter,
  active: {
    family: 'v2',
    activation_id: 'APV2A-1',
    selector_epoch: 2,
    action: 'bootstrap',
    created_at: '2026-09-02T00:00:00Z',
    actor_attribution: 'shared local operator credential',
    release: {
      id: 'APV2-1', policy_id: 'p', version: 1, title: 'Dual',
      what_to_escalate: 'Escalate scope changes.',
      what_not_to_escalate: 'Continue ordinary work.',
      digest: 'd', actor_attribution: 'shared local operator credential',
    },
  },
} as const;

describe('team escalation policy response contract', () => {
  beforeEach(() => vi.mocked(request).mockReset());

  it('narrows can_mutate to literal true for immutable release creation', async () => {
    expectTypeOf<TeamEscalationPolicyResponse['can_mutate']>().toEqualTypeOf<true>();
    vi.mocked(request).mockResolvedValue(empty);

    await expect(getTeamEscalationPolicy('alpha', 'engineering_manager'))
      .resolves.toEqual(empty);
  });

  it('accepts the empty, legacy_v1 and v2 discriminants', () => {
    expect(decodeTeamEscalationPolicyResponse(empty).family).toBe('empty');
    const decodedLegacy = decodeTeamEscalationPolicyResponse(legacy);
    expect(decodedLegacy.family).toBe('legacy_v1');
    if (decodedLegacy.family !== 'legacy_v1') throw new Error('expected legacy_v1');
    expect(decodedLegacy.active.release.normative_text).toBe('Text');
    const decodedV2 = decodeTeamEscalationPolicyResponse(v2);
    expect(decodedV2.family).toBe('v2');
    if (decodedV2.family !== 'v2') throw new Error('expected v2');
    expect(decodedV2.active.release.what_to_escalate).toBe('Escalate scope changes.');
  });

  it('rejects a server response that withdraws release creation', () => {
    expect(() => decodeTeamEscalationPolicyResponse({
      ...empty,
      can_mutate: false,
    })).toThrow('Invalid team escalation policy response');
  });

  it('rejects a response that omits the server-authored bootstrap member', () => {
    const { bootstrap_template: _omitted, ...withoutTemplate } = empty;
    expect(() => decodeTeamEscalationPolicyResponse(withoutTemplate))
      .toThrow('Invalid team escalation policy response');
  });

  it('accepts exact null bootstrap template and rejects every other malformed shape', () => {
    expect(decodeTeamEscalationPolicyResponse({ ...empty, team: 'content',
      target_manager: 'content_manager', bootstrap_template: null }).bootstrap_template).toBeNull();
    for (const malformed of [undefined, {}, [], '', 0]) {
      expect(() => decodeTeamEscalationPolicyResponse({ ...empty, bootstrap_template: malformed }))
        .toThrow('Invalid team escalation policy response');
    }
    expect(() => decodeTeamEscalationPolicyResponse({ ...empty, v2_starter: {} }))
      .toThrow('Invalid team escalation policy response');
  });

  it.each([
    ['missing family', () => { const { family: _f, ...rest } = empty; return { ...rest, active: undefined }; }],
    ['missing selector id', () => { const { selector_id: _s, ...rest } = empty; return rest; }],
    ['malformed selector id', () => ({ ...empty, selector_id: 'not-a-selector' })],
    ['legacy with v2 contract marker', () => ({ ...legacy, contract_version: 'v2' })],
    ['v2 release carrying v1 clause fields', () => ({
      ...v2, active: { ...v2.active, release: { ...v2.active.release, clauses: [] } },
    })],
    ['empty projection carrying an active selection', () => ({ ...empty, active: legacy.active })],
    ['v2 activation missing a required text', () => ({
      ...v2, active: { ...v2.active, release: { ...v2.active.release, what_to_escalate: undefined } },
    })],
    ['unknown family', () => ({ ...empty, family: 'legacy' })],
  ])('rejects malformed/mixed projection: %s', (_label, build) => {
    expect(() => decodeTeamEscalationPolicyResponse(build()))
      .toThrow('Invalid team escalation policy response');
  });

  it('threads the observed selector and exact payload for a legacy selection', async () => {
    vi.mocked(request).mockResolvedValue({});
    const body = {
      release_id: 'APR-2', expected_previous_epoch: 3, expected_selector_id: SELECTOR_ID,
      request_id: 'REQ-1', action: 'activate' as const,
      acknowledge_shared_credential_attribution: true as const,
    };
    await activateTeamEscalationPolicyRelease('alpha', 'engineering_manager', body);
    expect(vi.mocked(request)).toHaveBeenCalledWith(
      '/orgs/alpha/agents/engineering_manager/team-escalation-policy/activations',
      { method: 'POST', body },
    );
  });

  it('keeps explicit null and a missing selector distinct without substitution', async () => {
    vi.mocked(request).mockResolvedValue({});
    const withNull = {
      release_id: 'APR-2', expected_previous_epoch: 0, expected_selector_id: null,
      request_id: 'REQ-null', action: 'activate' as const,
      acknowledge_shared_credential_attribution: true as const,
    };
    await activateTeamEscalationPolicyRelease('alpha', 'engineering_manager', withNull);
    expect(vi.mocked(request)).toHaveBeenLastCalledWith(
      '/orgs/alpha/agents/engineering_manager/team-escalation-policy/activations',
      { method: 'POST', body: withNull },
    );
  });

  it('captures the exact coupled v2 paired payload without normalizing text', async () => {
    vi.mocked(request).mockResolvedValue({
      control: 'v2_create_activate', family: 'v2', contract_version: 'v2',
      selector_id: SELECTOR_ID, selector_epoch: 3, previous_selector_id: SELECTOR_ID,
      receipt: v2ControlReceipt('v2_create_activate'),
    });
    const body = {
      team: 'engineering' as const,
      policy_id: 'engineering-dual-text',
      title: '  Dual text  ',
      create_request_id: 'req-create-1',
      activation_request_id: 'req-activate-1',
      based_on_selector_id: SELECTOR_ID,
      expected_selector_id: SELECTOR_ID,
      action: 'activate' as const,
      what_to_escalate: 'Line one\nLine two',
      what_not_to_escalate: '  keep surrounding spaces  ',
      acknowledge_shared_credential_attribution: true as const,
    };
    const decoded = await createAndActivateTeamEscalationPolicyV2('alpha', 'engineering_manager', body);
    expect(vi.mocked(request)).toHaveBeenCalledWith(
      '/orgs/alpha/agents/engineering_manager/team-escalation-policy/v2/releases',
      { method: 'POST', body },
    );
    expect(body.title).toBe('  Dual text  ');
    expect(decoded.control).toBe('v2_create_activate');
  });

  it('maps the v2 activation route and rejects a malformed control receipt', async () => {
    vi.mocked(request).mockResolvedValue({
      control: 'v2_activate', family: 'v2', contract_version: 'v2',
      selector_id: SELECTOR_ID, selector_epoch: 3, previous_selector_id: SELECTOR_ID,
      receipt: v2ControlReceipt('v2_activate'),
    });
    const body = {
      team: 'engineering' as const, release_id: 'APV2-abc', request_id: 'req-1',
      expected_selector_id: SELECTOR_ID, action: 'reactivate_rollback' as const,
      acknowledge_shared_credential_attribution: true as const,
    };
    await expect(activateTeamEscalationPolicyV2('alpha', 'engineering_manager', body))
      .resolves.toMatchObject({ control: 'v2_activate' });
    expect(vi.mocked(request)).toHaveBeenCalledWith(
      '/orgs/alpha/agents/engineering_manager/team-escalation-policy/v2/activations',
      { method: 'POST', body },
    );

    vi.mocked(request).mockResolvedValue({ control: 'v2_activate', family: 'legacy_v1' });
    await expect(activateTeamEscalationPolicyV2('alpha', 'engineering_manager', body))
      .rejects.toThrow('Invalid authority policy v2 control response');
  });

  it('rejects a paired receipt whose immutable identity disagrees with the envelope', async () => {
    vi.mocked(request).mockResolvedValue({
      control: 'v2_create_activate', family: 'v2', contract_version: 'v2',
      selector_id: SELECTOR_ID, selector_epoch: 3, previous_selector_id: SELECTOR_ID,
      receipt: { ...v2ControlReceipt('v2_create_activate'), selector_epoch: 4 },
    });
    const body = {
      team: 'engineering' as const, policy_id: 'engineering-dual-text', title: 'Dual text',
      create_request_id: 'req-create-1', activation_request_id: 'req-activate-1',
      based_on_selector_id: SELECTOR_ID, expected_selector_id: SELECTOR_ID,
      action: 'activate' as const, what_to_escalate: 'Escalate.',
      what_not_to_escalate: 'Continue.', acknowledge_shared_credential_attribution: true as const,
    };
    await expect(createAndActivateTeamEscalationPolicyV2('alpha', 'engineering_manager', body))
      .rejects.toThrow('Invalid authority policy v2 control response');
  });
});
