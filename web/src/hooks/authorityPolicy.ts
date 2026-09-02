import { useData } from '@/design-system/providers/DataContext';

export const useTeamEscalationPolicy: ReturnType<typeof useData>['authorityPolicy']['useTeamEscalationPolicy'] =
  (agent) => useData().authorityPolicy.useTeamEscalationPolicy(agent);
