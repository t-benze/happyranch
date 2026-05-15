/**
 * Public, provider-aware orgs hooks. Compositions and layout chrome import
 * from here; never reach into the providers directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useOrgsList: ReturnType<typeof useData>['orgs']['useOrgsList'] = () =>
  useData().orgs.useOrgsList();
