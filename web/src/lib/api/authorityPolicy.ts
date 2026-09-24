import { request } from './client';

export interface AuthorityPolicyClause {
  id: string;
  category: string;
  condition: string;
  action: 'escalate_to_founder' | 'continue_same_root';
}

export interface AuthorityPolicyTemplate {
  title: string;
  normative_text: string;
  clauses: AuthorityPolicyClause[];
  continuation_phrase: string;
}

export type AuthorityPolicyFamily = 'empty' | 'legacy_v1' | 'v2';
export type AuthorityPolicyControlAction = 'bootstrap' | 'activate' | 'reactivate_rollback';
export const AUTHORITY_POLICY_ACTOR_ATTRIBUTION = 'shared local operator credential';
export const AUTHORITY_POLICY_V2_STARTER = {
  policy_id: 'engineering-dual-text',
  title: 'Engineering escalation policy',
  what_to_escalate: 'Escalate when the next action requires a product or external-contract change, significant architecture change, or substantial development effort beyond the approved scope. Also escalate decisions explicitly reserved for the founder that lack applicable authorization. Existing approval carries through ordinary implementation and recovery within its scope.',
  what_not_to_escalate: 'Continue implementation, debugging, review corrections, testing, CI waits, evidence collection and worker reassignment within approved scope. Failed reviews, retries, incomplete worker results and recoverable execution failures alone do not require founder escalation. Continue to enforce the required review, QA and merge gates.',
} as const;

export interface LegacyAuthorityPolicyRelease {
  id: string;
  policy_id: string;
  version: number;
  title: string;
  normative_text: string;
  clauses: AuthorityPolicyClause[];
  continuation_phrase: string;
  digest: string;
  created_at: string;
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

export interface V2AuthorityPolicyRelease {
  id: string;
  policy_id: string;
  version: number;
  title: string;
  what_to_escalate: string;
  what_not_to_escalate: string;
  digest: string;
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

export interface LegacyAuthorityPolicyActive {
  family: 'legacy_v1';
  activation_id: string;
  /** Legacy family epoch. Distinct from the selector epoch. */
  epoch: number;
  release: LegacyAuthorityPolicyRelease;
  action: AuthorityPolicyControlAction;
  created_at: string;
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

export interface V2AuthorityPolicyActive {
  family: 'v2';
  activation_id: string;
  /** Selector epoch — distinct from the legacy family epoch. */
  selector_epoch: number;
  release: V2AuthorityPolicyRelease;
  action: AuthorityPolicyControlAction;
  created_at: string;
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

interface TeamEscalationPolicyBase {
  team: 'engineering';
  target_manager: 'engineering_manager';
  /** Server authorization remains authoritative for both mutations. */
  can_mutate: true;
  bootstrap_template: AuthorityPolicyTemplate;
  /** Observed selector identity; required on every selection write. */
  selector_id: string;
  selector_epoch: number;
}

export interface EmptyTeamEscalationPolicyResponse extends TeamEscalationPolicyBase {
  family: 'empty';
  selector_epoch: 0;
  bootstrap_required: true;
}

export interface LegacyTeamEscalationPolicyResponse extends TeamEscalationPolicyBase {
  family: 'legacy_v1';
  contract_version: 'v1';
  active: LegacyAuthorityPolicyActive;
}

export interface V2TeamEscalationPolicyResponse extends TeamEscalationPolicyBase {
  family: 'v2';
  contract_version: 'v2';
  active: V2AuthorityPolicyActive;
}

export type TeamEscalationPolicyResponse =
  | EmptyTeamEscalationPolicyResponse
  | LegacyTeamEscalationPolicyResponse
  | V2TeamEscalationPolicyResponse;

export interface LegacyAuthorityPolicyHistoryItem {
  family: 'legacy_v1';
  contract_version: 'v1';
  release_id: string;
  policy_id: string;
  version: number;
  policy_digest: string;
  release_created_at: string;
  activation: null | { id: string; epoch: number; action: string; digest: string; created_at: string };
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

export interface AuthorityPolicyHistoryResponse {
  items: LegacyAuthorityPolicyHistoryItem[];
  next_cursor: string | null;
}

export interface V2AuthorityPolicyHistoryItem {
  family: 'v2';
  contract_version: 'v2';
  release_id: string;
  policy_id: string;
  version: number;
  title: string;
  what_to_escalate: string;
  what_not_to_escalate: string;
  contract_digest: string;
  policy_digest: string;
  release_created_at: string;
  activation: null | {
    id: string;
    selector_epoch: number;
    action: string;
    digest: string;
    created_at: string;
  };
  actor_attribution: typeof AUTHORITY_POLICY_ACTOR_ATTRIBUTION;
}

export interface AuthorityPolicyV2HistoryResponse {
  items: V2AuthorityPolicyHistoryItem[];
  next_cursor: string | null;
}

export interface AuthorityPolicyOutcomesResponse {
  items: Array<{ candidate_id: string; root_task_id: string; manager_session_id: string;
    causal_event_id: string; causal_result_id: string | null; release_id: string | null; activation_id: string | null;
    activation_epoch: number | null; policy_version: string; policy_digest: string;
    prompt_id: string; prompt_version: string; prompt_digest: string;
    provider_id: string | null; executor_kind: string | null; model_id: string;
    model_version: string; model_digest: string; disposition: string | null;
    disposition_code: string | null; evaluation_created_at: string | null;
    evaluator_contract: { id: string; version: string; digest: string };
    terminal_hook_outcome: string | null; thread_id: string | null;
    envelope: null | { id: string; state: string; consumed_at: string | null };
    receipt_state: 'complete' | 'receipt_incomplete' }>;
  next_cursor: string | null;
}

export interface CreateAuthorityPolicyReleaseRequest extends AuthorityPolicyTemplate {
  based_on_release_id: string | null;
  request_id: string;
}

export interface CreateAuthorityPolicyReleaseResponse {
  release: LegacyAuthorityPolicyRelease;
  activated: false;
  validation: { canonical: true; digest: string };
}

/**
 * Legacy v1 selection. ``expected_selector_id`` is REQUIRED by the backend and
 * is the observed selector base: absent is invalid, explicit ``null`` means the
 * client genuinely observed the empty selector. It is never today's live value
 * and never inferred from the legacy family epoch.
 */
export interface ActivateAuthorityPolicyReleaseRequest {
  release_id: string;
  expected_previous_epoch: number;
  expected_selector_id: string | null;
  request_id: string;
  action: 'activate' | 'reactivate_rollback';
  acknowledge_shared_credential_attribution: true;
}

/** Strict paired v2 save+activate. The two texts travel together. */
export interface V2PairedControlRequest {
  team: 'engineering';
  policy_id: string;
  title: string;
  create_request_id: string;
  activation_request_id: string;
  based_on_selector_id: string | null;
  expected_selector_id: string | null;
  action: 'bootstrap' | 'activate';
  what_to_escalate: string;
  what_not_to_escalate: string;
  acknowledge_shared_credential_attribution: true;
}

/** Select/rollback of an already-saved immutable v2 release. */
export interface V2ActivationControlRequest {
  team: 'engineering';
  release_id: string;
  request_id: string;
  expected_selector_id: string | null;
  action: 'bootstrap' | 'activate' | 'reactivate_rollback';
  acknowledge_shared_credential_attribution: true;
}

export interface V2AuthorityPolicyControlReceipt {
  team: 'engineering';
  kind: 'v2_create_activate' | 'v2_activate';
  create_request_id: string | null;
  create_request_digest: string | null;
  activation_request_id: string;
  activation_request_digest: string;
  release_id: string;
  policy_digest: string;
  release_version: number;
  activation_id: string;
  activation_digest: string;
  selector_id: string;
  selector_epoch: number;
  action: AuthorityPolicyControlAction;
  previous_selector_id: string | null;
  created_at: string;
}

export interface V2AuthorityPolicyControlResponse {
  control: 'v2_create_activate' | 'v2_activate';
  family: 'v2';
  contract_version: 'v2';
  selector_id: string;
  selector_epoch: number;
  previous_selector_id: string | null;
  receipt: V2AuthorityPolicyControlReceipt;
}

const INVALID_POLICY_RESPONSE = 'Invalid team escalation policy response';
const INVALID_V2_CONTROL_RESPONSE = 'Invalid authority policy v2 control response';
const APS_SELECTOR_ID = /^APS-[0-9a-f]{64}$/;
const APV2_RELEASE_ID = /^APV2-[0-9a-f]{64}$/;
const APV2_ACTIVATION_ID = /^APV2A-[0-9a-f]{64}$/;
const LOWER_HEX_DIGEST = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1;
}

function decodeClause(value: unknown, message: string): AuthorityPolicyClause {
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.id) ||
    !isNonEmptyString(value.category) ||
    !isNonEmptyString(value.condition) ||
    (value.action !== 'escalate_to_founder' && value.action !== 'continue_same_root')
  ) {
    throw new Error(message);
  }
  return {
    id: value.id,
    category: value.category,
    condition: value.condition,
    action: value.action,
  };
}

function decodeBootstrapTemplate(value: unknown, message: string): AuthorityPolicyTemplate {
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.title) ||
    !isNonEmptyString(value.normative_text) ||
    !Array.isArray(value.clauses) ||
    !isNonEmptyString(value.continuation_phrase)
  ) {
    throw new Error(message);
  }
  return {
    title: value.title,
    normative_text: value.normative_text,
    clauses: value.clauses.map((clause) => decodeClause(clause, message)),
    continuation_phrase: value.continuation_phrase,
  };
}

function decodeLegacyRelease(value: unknown): LegacyAuthorityPolicyRelease {
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.id) ||
    !isNonEmptyString(value.policy_id) ||
    !isPositiveInteger(value.version) ||
    !isNonEmptyString(value.title) ||
    !isNonEmptyString(value.normative_text) ||
    !Array.isArray(value.clauses) ||
    !isNonEmptyString(value.continuation_phrase) ||
    !isNonEmptyString(value.digest) ||
    !isNonEmptyString(value.created_at) ||
    value.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION ||
    // A legacy release must never carry v2 dual-text fields.
    'what_to_escalate' in value ||
    'what_not_to_escalate' in value
  ) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  return {
    id: value.id,
    policy_id: value.policy_id,
    version: value.version,
    title: value.title,
    normative_text: value.normative_text,
    clauses: value.clauses.map((clause) => decodeClause(clause, INVALID_POLICY_RESPONSE)),
    continuation_phrase: value.continuation_phrase,
    digest: value.digest,
    created_at: value.created_at,
    actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
  };
}

function decodeV2Release(value: unknown): V2AuthorityPolicyRelease {
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.id) ||
    !isNonEmptyString(value.policy_id) ||
    !isPositiveInteger(value.version) ||
    !isNonEmptyString(value.title) ||
    !isNonEmptyString(value.what_to_escalate) ||
    !isNonEmptyString(value.what_not_to_escalate) ||
    !isNonEmptyString(value.digest) ||
    value.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION ||
    // A v2 release must never be projected into the v1 clause shape.
    'clauses' in value ||
    'normative_text' in value ||
    'continuation_phrase' in value
  ) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  return {
    id: value.id,
    policy_id: value.policy_id,
    version: value.version,
    title: value.title,
    what_to_escalate: value.what_to_escalate,
    what_not_to_escalate: value.what_not_to_escalate,
    digest: value.digest,
    actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
  };
}

export function decodeTeamEscalationPolicyResponse(
  value: unknown,
): TeamEscalationPolicyResponse {
  if (
    !isRecord(value) ||
    value.can_mutate !== true ||
    value.team !== 'engineering' ||
    value.target_manager !== 'engineering_manager'
  ) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  const bootstrap_template = decodeBootstrapTemplate(value.bootstrap_template, INVALID_POLICY_RESPONSE);
  const family = value.family;
  if (family !== 'empty' && family !== 'legacy_v1' && family !== 'v2') {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  if (!isNonEmptyString(value.selector_id) || !APS_SELECTOR_ID.test(value.selector_id)) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  const selectorEpoch = value.selector_epoch;
  if (typeof selectorEpoch !== 'number' || !Number.isInteger(selectorEpoch) || selectorEpoch < 0) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }

  if (family === 'empty') {
    if (
      selectorEpoch !== 0 ||
      value.bootstrap_required !== true ||
      value.active !== undefined ||
      value.contract_version !== undefined
    ) {
      throw new Error(INVALID_POLICY_RESPONSE);
    }
    return {
      team: 'engineering',
      target_manager: 'engineering_manager',
      can_mutate: true,
      bootstrap_template,
      selector_id: value.selector_id,
      selector_epoch: 0,
      family: 'empty',
      bootstrap_required: true,
    };
  }

  const active = value.active;
  if (family === 'legacy_v1') {
    if (
      value.contract_version !== 'v1' ||
      !isRecord(active) ||
      active.family !== 'legacy_v1' ||
      !isNonEmptyString(active.activation_id) ||
      !isPositiveInteger(active.epoch) ||
      !isNonEmptyString(active.created_at) ||
      active.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION ||
      (active.action !== 'bootstrap' && active.action !== 'activate' &&
        active.action !== 'reactivate_rollback')
    ) {
      throw new Error(INVALID_POLICY_RESPONSE);
    }
    return {
      team: 'engineering',
      target_manager: 'engineering_manager',
      can_mutate: true,
      bootstrap_template,
      selector_id: value.selector_id,
      selector_epoch: selectorEpoch,
      family: 'legacy_v1',
      contract_version: 'v1',
      active: {
        family: 'legacy_v1',
        activation_id: active.activation_id,
        epoch: active.epoch,
        release: decodeLegacyRelease(active.release),
        action: active.action,
        created_at: active.created_at,
        actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
      },
    };
  }

  if (
    value.contract_version !== 'v2' ||
    !isRecord(active) ||
    active.family !== 'v2' ||
    !isNonEmptyString(active.activation_id) ||
    !isPositiveInteger(active.selector_epoch) ||
    !isNonEmptyString(active.created_at) ||
    active.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION ||
    (active.action !== 'bootstrap' && active.action !== 'activate' &&
      active.action !== 'reactivate_rollback')
  ) {
    throw new Error(INVALID_POLICY_RESPONSE);
  }
  return {
    team: 'engineering',
    target_manager: 'engineering_manager',
    can_mutate: true,
    bootstrap_template,
    selector_id: value.selector_id,
    selector_epoch: selectorEpoch,
    family: 'v2',
    contract_version: 'v2',
    active: {
      family: 'v2',
      activation_id: active.activation_id,
      selector_epoch: active.selector_epoch,
      release: decodeV2Release(active.release),
      action: active.action,
      created_at: active.created_at,
      actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
    },
  };
}

function decodeActivationSummary(
  value: unknown,
  epochKey: 'epoch' | 'selector_epoch',
): { summary: Record<string, unknown>; epoch: number } | null {
  if (value === null) return null;
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.id) ||
    !isPositiveInteger(value[epochKey]) ||
    !isNonEmptyString(value.action) ||
    !isNonEmptyString(value.digest) ||
    !isNonEmptyString(value.created_at)
  ) {
    throw new Error('Invalid authority policy history response');
  }
  return { summary: value, epoch: value[epochKey] as number };
}

export function decodeAuthorityPolicyHistoryResponse(
  value: unknown,
): AuthorityPolicyHistoryResponse {
  const message = 'Invalid authority policy history response';
  if (!isRecord(value) || !Array.isArray(value.items)) throw new Error(message);
  if (value.next_cursor !== null && typeof value.next_cursor !== 'string') throw new Error(message);
  const items = value.items.map((item): LegacyAuthorityPolicyHistoryItem => {
    if (
      !isRecord(item) ||
      item.family !== 'legacy_v1' ||
      item.contract_version !== 'v1' ||
      !isNonEmptyString(item.release_id) ||
      !isNonEmptyString(item.policy_id) ||
      !isPositiveInteger(item.version) ||
      !isNonEmptyString(item.policy_digest) ||
      !isNonEmptyString(item.release_created_at) ||
      item.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION
    ) {
      throw new Error(message);
    }
    const activation = decodeActivationSummary(item.activation, 'epoch');
    return {
      family: 'legacy_v1',
      contract_version: 'v1',
      release_id: item.release_id,
      policy_id: item.policy_id,
      version: item.version,
      policy_digest: item.policy_digest,
      release_created_at: item.release_created_at,
      activation: activation === null ? null : {
        id: activation.summary.id as string,
        epoch: activation.epoch,
        action: activation.summary.action as string,
        digest: activation.summary.digest as string,
        created_at: activation.summary.created_at as string,
      },
      actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
    };
  });
  return { items, next_cursor: value.next_cursor as string | null };
}

export function decodeAuthorityPolicyV2HistoryResponse(
  value: unknown,
): AuthorityPolicyV2HistoryResponse {
  const message = 'Invalid authority policy v2 history response';
  if (!isRecord(value) || !Array.isArray(value.items)) throw new Error(message);
  if (value.next_cursor !== null && typeof value.next_cursor !== 'string') throw new Error(message);
  const items = value.items.map((item): V2AuthorityPolicyHistoryItem => {
    if (
      !isRecord(item) ||
      item.family !== 'v2' ||
      item.contract_version !== 'v2' ||
      !isNonEmptyString(item.release_id) ||
      !isNonEmptyString(item.policy_id) ||
      !isPositiveInteger(item.version) ||
      !isNonEmptyString(item.title) ||
      !isNonEmptyString(item.what_to_escalate) ||
      !isNonEmptyString(item.what_not_to_escalate) ||
      !isNonEmptyString(item.contract_digest) ||
      !isNonEmptyString(item.policy_digest) ||
      !isNonEmptyString(item.release_created_at) ||
      item.actor_attribution !== AUTHORITY_POLICY_ACTOR_ATTRIBUTION
    ) {
      throw new Error(message);
    }
    const activation = decodeActivationSummary(item.activation, 'selector_epoch');
    return {
      family: 'v2',
      contract_version: 'v2',
      release_id: item.release_id,
      policy_id: item.policy_id,
      version: item.version,
      title: item.title,
      what_to_escalate: item.what_to_escalate,
      what_not_to_escalate: item.what_not_to_escalate,
      contract_digest: item.contract_digest,
      policy_digest: item.policy_digest,
      release_created_at: item.release_created_at,
      activation: activation === null ? null : {
        id: activation.summary.id as string,
        selector_epoch: activation.epoch,
        action: activation.summary.action as string,
        digest: activation.summary.digest as string,
        created_at: activation.summary.created_at as string,
      },
      actor_attribution: AUTHORITY_POLICY_ACTOR_ATTRIBUTION,
    };
  });
  return { items, next_cursor: value.next_cursor as string | null };
}

export function decodeV2AuthorityPolicyControlResponse(
  value: unknown,
): V2AuthorityPolicyControlResponse {
  if (
    !isRecord(value) ||
    (value.control !== 'v2_create_activate' && value.control !== 'v2_activate') ||
    value.family !== 'v2' ||
    value.contract_version !== 'v2' ||
    !isNonEmptyString(value.selector_id) ||
    !APS_SELECTOR_ID.test(value.selector_id) ||
    !isPositiveInteger(value.selector_epoch) ||
    !(value.previous_selector_id === null ||
      (isNonEmptyString(value.previous_selector_id) && APS_SELECTOR_ID.test(value.previous_selector_id))) ||
    !isRecord(value.receipt)
  ) {
    throw new Error(INVALID_V2_CONTROL_RESPONSE);
  }
  const receipt = value.receipt;
  if (
    receipt.team !== 'engineering' ||
    (receipt.kind !== 'v2_create_activate' && receipt.kind !== 'v2_activate') ||
    !(receipt.create_request_id === null || isNonEmptyString(receipt.create_request_id)) ||
    !(receipt.create_request_digest === null ||
      (isNonEmptyString(receipt.create_request_digest) && LOWER_HEX_DIGEST.test(receipt.create_request_digest))) ||
    (receipt.create_request_id === null) !== (receipt.create_request_digest === null) ||
    !isNonEmptyString(receipt.activation_request_id) ||
    !isNonEmptyString(receipt.activation_request_digest) ||
    !LOWER_HEX_DIGEST.test(receipt.activation_request_digest) ||
    !isNonEmptyString(receipt.release_id) || !APV2_RELEASE_ID.test(receipt.release_id) ||
    !isNonEmptyString(receipt.policy_digest) || !LOWER_HEX_DIGEST.test(receipt.policy_digest) ||
    receipt.release_id !== `APV2-${receipt.policy_digest}` ||
    !isPositiveInteger(receipt.release_version) ||
    !isNonEmptyString(receipt.activation_id) || !APV2_ACTIVATION_ID.test(receipt.activation_id) ||
    !isNonEmptyString(receipt.activation_digest) || !LOWER_HEX_DIGEST.test(receipt.activation_digest) ||
    receipt.activation_id !== `APV2A-${receipt.activation_digest}` ||
    !isNonEmptyString(receipt.selector_id) || !APS_SELECTOR_ID.test(receipt.selector_id) ||
    !isPositiveInteger(receipt.selector_epoch) ||
    (receipt.action !== 'bootstrap' && receipt.action !== 'activate' &&
      receipt.action !== 'reactivate_rollback') ||
    !(receipt.previous_selector_id === null ||
      (isNonEmptyString(receipt.previous_selector_id) && APS_SELECTOR_ID.test(receipt.previous_selector_id))) ||
    !isNonEmptyString(receipt.created_at) ||
    receipt.selector_id !== value.selector_id ||
    receipt.selector_epoch !== value.selector_epoch ||
    receipt.previous_selector_id !== value.previous_selector_id ||
    receipt.kind !== value.control ||
    (value.control === 'v2_create_activate' && receipt.create_request_id === null)
  ) {
    throw new Error(INVALID_V2_CONTROL_RESPONSE);
  }
  return value as unknown as V2AuthorityPolicyControlResponse;
}

export const getTeamEscalationPolicy = (
  slug: string,
  agentName: string,
): Promise<TeamEscalationPolicyResponse> =>
  request<unknown>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy`)
    .then(decodeTeamEscalationPolicyResponse);

export const createTeamEscalationPolicyRelease = (
  slug: string,
  agentName: string,
  body: CreateAuthorityPolicyReleaseRequest,
): Promise<CreateAuthorityPolicyReleaseResponse> =>
  request(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/releases`, {
    method: 'POST', body,
  });

export const activateTeamEscalationPolicyRelease = (
  slug: string,
  agentName: string,
  body: ActivateAuthorityPolicyReleaseRequest,
): Promise<unknown> => request(
  `/orgs/${slug}/agents/${agentName}/team-escalation-policy/activations`,
  { method: 'POST', body },
);

export const createAndActivateTeamEscalationPolicyV2 = (
  slug: string,
  agentName: string,
  body: V2PairedControlRequest,
): Promise<V2AuthorityPolicyControlResponse> =>
  request<unknown>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/v2/releases`, {
    method: 'POST', body,
  }).then(decodeV2AuthorityPolicyControlResponse);

export const activateTeamEscalationPolicyV2 = (
  slug: string,
  agentName: string,
  body: V2ActivationControlRequest,
): Promise<V2AuthorityPolicyControlResponse> =>
  request<unknown>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/v2/activations`, {
    method: 'POST', body,
  }).then(decodeV2AuthorityPolicyControlResponse);

export const getTeamEscalationPolicyHistory = (slug: string, agentName: string, cursor?: string) =>
  request<unknown>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/history?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
    .then(decodeAuthorityPolicyHistoryResponse);

export const getTeamEscalationPolicyV2History = (slug: string, agentName: string, cursor?: string) =>
  request<unknown>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/v2/history?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`)
    .then(decodeAuthorityPolicyV2HistoryResponse);

export const getTeamEscalationPolicyOutcomes = (slug: string, agentName: string, cursor?: string) =>
  request<AuthorityPolicyOutcomesResponse>(`/orgs/${slug}/agents/${agentName}/team-escalation-policy/outcomes?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);

/** Family-aware epoch for display; selector epoch is distinct from the legacy epoch. */
export const authorityPolicyActiveEpoch = (
  active: LegacyAuthorityPolicyActive | V2AuthorityPolicyActive,
): number => (active.family === 'legacy_v1' ? active.epoch : active.selector_epoch);

export const isEligiblePolicyManager = (agent: {
  name: string;
  team: string;
  role: string;
} | undefined): boolean =>
  // Structurally reusable seam; the current Engineering allowlist remains explicit.
  agent?.name === 'engineering_manager' &&
  agent.team === 'engineering' &&
  agent.role === 'manager';
