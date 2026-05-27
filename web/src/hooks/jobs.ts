/**
 * Public, provider-aware jobs hooks.
 *
 * Compositions in `features/` import from this file — they never reach into
 * `design-system/providers/` directly.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useJobsRoutes = () => useData().useJobsRoutes();

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const useJobsList: ReturnType<typeof useData>['jobs']['useJobsList'] = (
  params,
) => useData().jobs.useJobsList(params);

export const useJob: ReturnType<typeof useData>['jobs']['useJob'] = (
  jobId,
) => useData().jobs.useJob(jobId);

export const useJobOutput: ReturnType<typeof useData>['jobs']['useJobOutput'] = (
  jobId,
) => useData().jobs.useJobOutput(jobId);

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useRejectJob: ReturnType<typeof useData>['jobs']['useRejectJob'] = () =>
  useData().jobs.useRejectJob();

export const useRunJob: ReturnType<typeof useData>['jobs']['useRunJob'] = () =>
  useData().jobs.useRunJob();

export const useStopJob: ReturnType<typeof useData>['jobs']['useStopJob'] = () =>
  useData().jobs.useStopJob();
