import { request } from './client';

export interface AuthorityPolicyClause {
  id: string;
  category: string;
  condition: string;
  action: 'escalate_to_founder' | 'continue_same_root';
}

export interface TeamEscalationPolicyResponse {
  team: 'engineering';
  target_manager: 'engineering_manager';
  /** Immutable release creation is exposed; activation remains guard-closed. */
  can_mutate: true;
  bootstrap_required?: true;
  activation_guard: { ready: boolean; reason: string };
  bootstrap_template: AuthorityPolicyTemplate;
  active?: {
    activation_id: string;
    epoch: number;
    action: 'bootstrap' | 'activate' | 'reactivate_rollback';
    created_at: string;
    actor_attribution: 'shared local operator credential';
    release: {
      id: string;
      policy_id: string;
      version: number;
      title: string;
      normative_text: string;
      clauses: AuthorityPolicyClause[];
      continuation_phrase: string;
      digest: string;
      created_at: string;
      actor_attribution: 'shared local operator credential';
    };
  };
}

export interface AuthorityPolicyTemplate {
  title: string;
  normative_text: string;
  clauses: AuthorityPolicyClause[];
  continuation_phrase: string;
}

export interface CreateAuthorityPolicyReleaseRequest extends AuthorityPolicyTemplate {
  based_on_release_id: string | null;
  request_id: string;
}

export interface CreateAuthorityPolicyReleaseResponse {
  release: TeamEscalationPolicyResponse['active'] extends infer _T ? {
    id: string; policy_id: string; version: number; title: string;
    normative_text: string; clauses: AuthorityPolicyClause[];
    continuation_phrase: string; digest: string; created_at: string;
    actor_attribution: 'shared local operator credential';
  } : never;
  activated: false;
  validation: { canonical: true; digest: string };
}

export function decodeTeamEscalationPolicyResponse(
  value: unknown,
): TeamEscalationPolicyResponse {
  if (
    !value ||
    typeof value !== 'object' ||
    (value as { can_mutate?: unknown }).can_mutate !== true ||
    !(value as { bootstrap_template?: unknown }).bootstrap_template ||
    typeof (value as { bootstrap_template: { title?: unknown } }).bootstrap_template.title !== 'string' ||
    typeof (value as { bootstrap_template: { normative_text?: unknown } }).bootstrap_template.normative_text !== 'string' ||
    !Array.isArray((value as { bootstrap_template: { clauses?: unknown } }).bootstrap_template.clauses) ||
    typeof (value as { bootstrap_template: { continuation_phrase?: unknown } }).bootstrap_template.continuation_phrase !== 'string'
  ) {
    throw new Error('Invalid team escalation policy response');
  }
  return value as TeamEscalationPolicyResponse;
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
  body: { release_id: string; expected_previous_epoch: number; request_id: string;
    action: 'activate' | 'reactivate_rollback'; acknowledge_shared_credential_attribution: true },
): Promise<unknown> => request(
  `/orgs/${slug}/agents/${agentName}/team-escalation-policy/activations`,
  { method: 'POST', body },
);

export const isEligiblePolicyManager = (agent: {
  name: string;
  team: string;
  role: string;
} | undefined): boolean =>
  agent?.name === 'engineering_manager' &&
  agent.team === 'engineering' &&
  agent.role === 'manager';
