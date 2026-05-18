/**
 * Public, provider-aware agents hooks. Compositions import from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useAgentsList: ReturnType<typeof useData>['agents']['useAgentsList'] = () =>
  useData().agents.useAgentsList();
