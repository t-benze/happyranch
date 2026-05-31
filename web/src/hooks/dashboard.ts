/**
 * Public, provider-aware dashboard hooks.
 *
 * Every hook is a one-liner that reads `useData().dashboard` and forwards.
 * Compositions in `features/` and `prototypes/` import from this file —
 * they never reach into `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useDashboardSummary: ReturnType<
  typeof useData
>['dashboard']['useDashboardSummary'] = () =>
  useData().dashboard.useDashboardSummary();
