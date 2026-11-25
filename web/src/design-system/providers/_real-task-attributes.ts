/**
 * Real task-attribute definitions slice (THR-390 PR6) over the PR2 typed
 * client. Query keys are org-scoped: `[org, 'task-attributes', ...]`, so an
 * org switch never shows another org's rows or reads.
 *
 * - The list is one GET with archived definitions included.
 * - Row-authority reads and history pages share one per-QueryClient limiter
 *   (at most 4 per-definition GETs in flight).
 * - `useDefinition` always re-reads on mount: a dialog never takes authority
 *   from a cached prefetch (MEM-300).
 * - No `refetchInterval`: these reads are never polled.
 * - Mutations are plain sends: never retried, never replayed.
 */
import { useQueries, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { taskAttributes } from '@/lib/api';
import type { AttributeDefinitionRead, AttributeDefinitionsRead } from '@/lib/api/taskAttributeTypes';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TaskAttributeActions, TaskAttributesApi, TaskAttributesQuery } from './DataContext';

export const taskAttributeKeys = {
  all: (slug: string) => [slug, 'task-attributes'] as const,
  list: (slug: string) => [slug, 'task-attributes', 'list'] as const,
  definition: (slug: string, id: string) => [slug, 'task-attributes', 'definition', id] as const,
  versions: (slug: string, id: string, versions: readonly number[]) =>
    [slug, 'task-attributes', 'versions', id, versions.join(',')] as const,
};

function useSlug(): string {
  const scoped = useOrgSlugOptional();
  const { slug } = useParams<{ slug: string }>();
  return scoped ?? slug ?? '';
}

/** At most `max` tasks run at once; the rest wait in FIFO order. */
function createLimiter(max: number): <T>(run: () => Promise<T>) => Promise<T> {
  let active = 0;
  const waiting: (() => void)[] = [];
  const release = (): void => {
    active -= 1;
    waiting.shift()?.();
  };
  return async <T>(run: () => Promise<T>): Promise<T> => {
    if (active >= max) await new Promise<void>((resolve) => waiting.push(resolve));
    active += 1;
    try {
      return await run();
    } finally {
      release();
    }
  };
}

const limiters = new WeakMap<QueryClient, ReturnType<typeof createLimiter>>();
function limiterFor(qc: QueryClient): ReturnType<typeof createLimiter> {
  let limiter = limiters.get(qc);
  if (!limiter) {
    limiter = createLimiter(4);
    limiters.set(qc, limiter);
  }
  return limiter;
}

type Shaped<T> = {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  isSuccess: boolean;
  isFetching: boolean;
  dataUpdatedAt: number;
  refetch: () => Promise<unknown>;
};
const shape = <T>(q: Shaped<T>): TaskAttributesQuery<T> => ({
  data: q.data,
  isLoading: q.isLoading,
  isError: q.isError,
  error: q.error,
  isSuccess: q.isSuccess,
  isFetching: q.isFetching,
  dataUpdatedAt: q.dataUpdatedAt,
  refetch: q.refetch,
});

export const realTaskAttributesApi: TaskAttributesApi = {
  useDefinitionList: () => {
    const slug = useSlug();
    return shape(useQuery({
      queryKey: taskAttributeKeys.list(slug),
      queryFn: () => taskAttributes.listDefinitions(slug, { include_archived: true }),
      enabled: !!slug,
    }));
  },
  useDefinitionReads: (ids, enabled) => {
    const slug = useSlug();
    const qc = useQueryClient();
    const limit = limiterFor(qc);
    const results = useQueries({
      queries: ids.map((id) => ({
        queryKey: taskAttributeKeys.definition(slug, id),
        queryFn: () => limit(() => taskAttributes.getDefinition(slug, id)),
        enabled: enabled && !!slug,
      })),
    });
    const byId: Record<string, TaskAttributesQuery<AttributeDefinitionRead>> = {};
    ids.forEach((id, index) => {
      const result = results[index];
      if (result) byId[id] = shape(result);
    });
    return byId;
  },
  useDefinition: (definitionId) => {
    const slug = useSlug();
    return shape(useQuery({
      queryKey: taskAttributeKeys.definition(slug, definitionId ?? ''),
      queryFn: () => taskAttributes.getDefinition(slug, definitionId ?? ''),
      enabled: !!slug && !!definitionId,
      refetchOnMount: 'always',
    }));
  },
  useDefinitionVersions: (definitionId, versions, enabled) => {
    const slug = useSlug();
    const qc = useQueryClient();
    const limit = limiterFor(qc);
    return shape(useQuery({
      queryKey: taskAttributeKeys.versions(slug, definitionId, versions),
      // All-or-nothing: one failed version GET fails the whole page.
      queryFn: () => Promise.all(versions.map((version) =>
        limit(() => taskAttributes.getDefinition(slug, definitionId, { version })))),
      enabled: enabled && !!slug && versions.length > 0,
      staleTime: Infinity,
    }));
  },
  useTaskAttributeActions: () => {
    const slug = useSlug();
    const qc = useQueryClient();
    const actions: TaskAttributeActions = {
      create: (body) => taskAttributes.createDefinition(slug, { ...body, expected_version: 0 }),
      update: (definitionId, body) => taskAttributes.updateDefinition(slug, definitionId, body),
      archive: (definitionId, expectedVersion) => taskAttributes.archiveDefinition(slug, definitionId, expectedVersion),
      readList: () => qc.fetchQuery<AttributeDefinitionsRead>({
        queryKey: taskAttributeKeys.list(slug),
        queryFn: () => taskAttributes.listDefinitions(slug, { include_archived: true }),
        staleTime: 0,
      }),
      readDefinition: (definitionId) => qc.fetchQuery<AttributeDefinitionRead>({
        queryKey: taskAttributeKeys.definition(slug, definitionId),
        queryFn: () => taskAttributes.getDefinition(slug, definitionId),
        staleTime: 0,
      }),
      isDefinitionCurrent: (definitionId) => {
        const state = qc.getQueryState(taskAttributeKeys.definition(slug, definitionId));
        return state?.status === 'success' && state.fetchStatus === 'idle';
      },
      // After a confirmed write: the list and every per-definition read of this org.
      refreshList: () => qc.invalidateQueries({ queryKey: taskAttributeKeys.all(slug) }),
    };
    return actions;
  },
};
