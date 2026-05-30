/**
 * Public, provider-aware teams hook. Compositions import from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useTeamsList: ReturnType<typeof useData>['teams']['useTeamsList'] = () =>
  useData().teams.useTeamsList();
