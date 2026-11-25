/**
 * Real task-attribute VALUES slice (THR-390 PR7) over the PR2 typed client.
 *
 * - Query key `[org, 'task-attribute-values', taskId]`: org-scoped, and
 *   disjoint from PR6's `[org, 'task-attributes', ...]` definition keys.
 * - The browser never sends `session_id` (it is always the Founder path).
 * - `fresh` observers (editor, history) re-read on mount: a dialog never
 *   takes authority from the cache (MEM-300).
 * - No `refetchInterval`; reads wait while a native recovery state is active.
 * - The PUT is a plain send: never retried, never replayed.
 */
import { useSyncExternalStore } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { taskAttributes } from '@/lib/api';
import type { TaskAttributesRead } from '@/lib/api/taskAttributeTypes';
import { getNativeRecoveryState, subscribeNativeRecovery } from '@/lib/nativeRecovery';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TaskAttributeValueActions, TaskAttributeValuesApi, TaskAttributesQuery } from './DataContext';

export const taskAttributeValueKeys = {
  task: (slug: string, taskId: string) => [slug, 'task-attribute-values', taskId] as const,
};

function useSlug(): string {
  const scoped = useOrgSlugOptional();
  const { slug } = useParams<{ slug: string }>();
  return scoped ?? slug ?? '';
}

export const realTaskAttributeValuesApi: TaskAttributeValuesApi = {
  useTaskAttributes: (taskId, fresh = false) => {
    const slug = useSlug();
    const recovery = useSyncExternalStore(subscribeNativeRecovery, getNativeRecoveryState, getNativeRecoveryState);
    const q = useQuery({
      queryKey: taskAttributeValueKeys.task(slug, taskId),
      queryFn: () => taskAttributes.getAttributes(slug, taskId),
      enabled: !!slug && !!taskId && recovery === null,
      refetchOnMount: fresh ? 'always' : true,
      refetchOnWindowFocus: false,
      retry: false,
    });
    const shaped: TaskAttributesQuery<TaskAttributesRead> = {
      data: q.data, isLoading: q.isLoading, isError: q.isError, error: q.error, isSuccess: q.isSuccess,
      isFetching: q.isFetching, dataUpdatedAt: q.dataUpdatedAt, refetch: q.refetch,
    };
    return shaped;
  },
  useTaskAttributeValueActions: (taskId) => {
    const slug = useSlug();
    const qc = useQueryClient();
    const key = taskAttributeValueKeys.task(slug, taskId);
    const actions: TaskAttributeValueActions = {
      save: (changes) => taskAttributes.saveAttributes(slug, taskId, changes),
      read: () => qc.fetchQuery<TaskAttributesRead>({
        queryKey: key,
        queryFn: () => taskAttributes.getAttributes(slug, taskId),
        staleTime: 0,
        retry: false,
      }),
      isCurrent: () => {
        const state = qc.getQueryState(key);
        return state?.status === 'success' && state.fetchStatus === 'idle';
      },
      accept: (read) => qc.setQueryData(key, read),
    };
    return actions;
  },
};
