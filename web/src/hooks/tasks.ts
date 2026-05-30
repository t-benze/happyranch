/**
 * Public, provider-aware tasks hooks.
 *
 * Every hook is a one-liner that reads `useData().tasks` and forwards.
 * Compositions in `features/` and `prototypes/` import from this file —
 * they never reach into `design-system/providers/` directly.
 *
 * The slug is intentionally not an argument; the provider knows its own
 * active org. Compositions that need the slug for URL navigation should
 * use `useTasksRoutes()` (provider-aware) below.
 */
import { useData } from '@/design-system/providers/DataContext';

/**
 * Provider-aware route builder for the tasks feature. Compositions
 * use this instead of hardcoding `/orgs/${slug}/tasks/...` paths,
 * so the same JSX works under both the real and the prototype routes.
 */
export const useTasksRoutes = () => useData().useTasksRoutes();

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export const useTasksList: ReturnType<typeof useData>['tasks']['useTasksList'] = (
  params,
) => useData().tasks.useTasksList(params);

export const useTasksInfiniteList: ReturnType<typeof useData>['tasks']['useTasksInfiniteList'] = (
  params,
) => useData().tasks.useTasksInfiniteList(params);

export const useTask: ReturnType<typeof useData>['tasks']['useTask'] = (
  taskId,
) => useData().tasks.useTask(taskId);

export const useTaskRecall: ReturnType<typeof useData>['tasks']['useTaskRecall'] = (
  taskId,
) => useData().tasks.useTaskRecall(taskId);

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export const useTaskTailSSE: ReturnType<typeof useData>['tasks']['useTaskTailSSE'] = (
  taskId,
  onEvent,
) => useData().tasks.useTaskTailSSE(taskId, onEvent);

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export const useCancelTask: ReturnType<typeof useData>['tasks']['useCancelTask'] = (
  taskId,
) => useData().tasks.useCancelTask(taskId);

export const useRevisitTask: ReturnType<typeof useData>['tasks']['useRevisitTask'] = (
  taskId,
) => useData().tasks.useRevisitTask(taskId);

export const useResolveEscalation: ReturnType<typeof useData>['tasks']['useResolveEscalation'] = (
  taskId,
) => useData().tasks.useResolveEscalation(taskId);
