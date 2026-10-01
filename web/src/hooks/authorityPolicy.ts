import { useData } from '@/design-system/providers/DataContext';
export type {
  AuthorityPolicyTemplate,
  TeamEscalationPolicyResponse,
  V2AuthorityPolicyControlResponse,
  V2PairedControlRequest,
} from '@/lib/api/authorityPolicy';
export {
  authorityPolicyActiveEpoch,
  isEligiblePolicyManager,
} from '@/lib/api/authorityPolicy';

export const useTeamEscalationPolicy: ReturnType<typeof useData>['authorityPolicy']['useTeamEscalationPolicy'] =
  (agent) => useData().authorityPolicy.useTeamEscalationPolicy(agent);
export const useCreateTeamEscalationPolicyRelease = () =>
  useData().authorityPolicy.useCreateTeamEscalationPolicyRelease();
export const useActivateTeamEscalationPolicyRelease = () =>
  useData().authorityPolicy.useActivateTeamEscalationPolicyRelease();
export const useCreateTeamEscalationPolicyV2Release = () =>
  useData().authorityPolicy.useCreateTeamEscalationPolicyV2Release();
export const useActivateTeamEscalationPolicyV2Release = () =>
  useData().authorityPolicy.useActivateTeamEscalationPolicyV2Release();
export const useTeamEscalationPolicyHistory = (agent: { name: string; team: string; role: string }) =>
  useData().authorityPolicy.useTeamEscalationPolicyHistory(agent);
export const useTeamEscalationPolicyV2History = (agent: { name: string; team: string; role: string }) =>
  useData().authorityPolicy.useTeamEscalationPolicyV2History(agent);
export const useTeamEscalationPolicyOutcomes = (agent: { name: string; team: string; role: string }) =>
  useData().authorityPolicy.useTeamEscalationPolicyOutcomes(agent);
