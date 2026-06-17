/**
 * Public, provider-aware Dreams hooks. Each is a one-liner that reads
 * `useData().dreams` and forwards. Compositions in `features/dreams/` import
 * only from this file.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useDreamsRoutes = () => useData().useDreamsRoutes();

export const useDreamsList: ReturnType<typeof useData>['dreams']['useDreamsList'] = (
  params,
) => useData().dreams.useDreamsList(params);

export const useDream: ReturnType<typeof useData>['dreams']['useDream'] = (
  dreamId,
) => useData().dreams.useDream(dreamId);

export const useAcceptCandidate: ReturnType<
  typeof useData
>['dreams']['useAcceptCandidate'] = () => useData().dreams.useAcceptCandidate();

export const useDismissCandidate: ReturnType<
  typeof useData
>['dreams']['useDismissCandidate'] = () => useData().dreams.useDismissCandidate();
