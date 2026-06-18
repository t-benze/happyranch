/**
 * Public, provider-aware schedule hook. Mirrors `useData().workHours` so
 * compositions never reach into `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useWorkHoursList: ReturnType<typeof useData>['workHours']['useWorkHoursList'] = (
  params,
) => useData().workHours.useWorkHoursList(params);
