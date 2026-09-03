import { useData } from '@/design-system/providers/DataContext';
export type { AuthorityPolicyTemplate } from '@/lib/api/authorityPolicy';

export const useTeamEscalationPolicy: ReturnType<typeof useData>['authorityPolicy']['useTeamEscalationPolicy'] =
  (agent) => useData().authorityPolicy.useTeamEscalationPolicy(agent);
export const useCreateTeamEscalationPolicyRelease = () =>
  useData().authorityPolicy.useCreateTeamEscalationPolicyRelease();
export const useActivateTeamEscalationPolicyRelease = () =>
  useData().authorityPolicy.useActivateTeamEscalationPolicyRelease();
