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
  can_mutate: boolean;
  bootstrap_required?: true;
  activation_guard: { ready: false; reason: string };
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

export const getTeamEscalationPolicy = (
  slug: string,
  agentName: string,
): Promise<TeamEscalationPolicyResponse> =>
  request(`/orgs/${slug}/agents/${agentName}/team-escalation-policy`);

export const isEligiblePolicyManager = (agent: {
  name: string;
  team: string;
  role: string;
} | undefined): boolean =>
  agent?.name === 'engineering_manager' &&
  agent.team === 'engineering' &&
  agent.role === 'manager';
