/** Deterministic prototype model only (THR-390 PR7). These observations are not daemon/DB proof. */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/lib/api';
import type { AttributeChange, TaskAttributesRead } from '@/lib/api/taskAttributeTypes';
import type { TaskAttributeValueActions, TaskAttributeValuesApi, TaskAttributesQuery } from './DataContext';
import {
  DEFAULT_TASK_ATTRIBUTE_VALUE_FIXTURE,
  TASK_ATTRIBUTE_VALUE_FIXTURES,
  type TaskAttributeValueFixture,
} from './_task-attribute-value-fixtures';

interface Model {
  fixture: TaskAttributeValueFixture;
  reads: number;
  current: TaskAttributesRead;
}
const MODEL_KEY = ['mock-task-attribute-values-model'];
const READ_KEY = (taskId: string) => ['mock-task-attribute-values', taskId];

function model(qc: QueryClient): Model {
  const cached = qc.getQueryData<Model>(MODEL_KEY);
  if (cached) return cached;
  const fixture = structuredClone(TASK_ATTRIBUTE_VALUE_FIXTURES[DEFAULT_TASK_ATTRIBUTE_VALUE_FIXTURE]) as TaskAttributeValueFixture;
  const initial: Model = { fixture, reads: 0, current: fixture.read };
  qc.setQueryData(MODEL_KEY, initial);
  return initial;
}

function read(m: Model, taskId: string): TaskAttributesRead {
  m.reads += 1;
  if (m.reads > 1 && m.fixture.reread) m.current = m.fixture.reread;
  return { ...structuredClone(m.current), task_id: taskId };
}

function apply(m: Model, changes: AttributeChange[]): TaskAttributesRead {
  const answer = m.fixture.save;
  if (answer && answer.status >= 400) {
    const detail = (answer.body as { detail?: { code?: string } }).detail;
    throw new ApiError(answer.status, detail?.code ?? null, detail ?? null);
  }
  const next = structuredClone(m.current);
  for (const c of changes) {
    const a = next.attributes.find((x) => x.definition.definition_id === c.definition_id);
    if (!a) continue;
    const revision = a.current.revision + 1;
    const old = a.current.present ? { present: true as const, value: a.current.value } : { present: false as const };
    const nv = c.op === 'set' ? { present: true as const, value: c.value } : { present: false as const };
    a.current = { ...nv, revision };
    a.history.events.push({
      event_id: Date.now(), definition_id: a.definition.definition_id, definition_version: a.definition.version, definition: a.definition,
      revision, actor_id: 'founder', actor_kind: 'founder', actor: { canonical_id: 'founder', kind: 'founder', addressable_name: null },
      at: new Date().toISOString(), source: c.op === 'set' ? 'edit' : 'clear', old, new: nv,
    });
  }
  m.current = next;
  return structuredClone(next);
}

export const mockTaskAttributeValuesApi: TaskAttributeValuesApi = {
  useTaskAttributes: (taskId, fresh = false) => {
    const qc = useQueryClient();
    const q = useQuery({
      queryKey: READ_KEY(taskId),
      queryFn: () => read(model(qc), taskId),
      enabled: !!taskId,
      refetchOnMount: fresh ? 'always' : true,
      retry: false,
    });
    const shaped: TaskAttributesQuery<TaskAttributesRead> = {
      data: q.data, isLoading: q.isLoading, isError: q.isError, error: q.error, isSuccess: q.isSuccess,
      isFetching: q.isFetching, dataUpdatedAt: q.dataUpdatedAt, refetch: q.refetch,
    };
    return shaped;
  },
  useTaskAttributeValueActions: (taskId) => {
    const qc = useQueryClient();
    const actions: TaskAttributeValueActions = {
      save: async (changes) => apply(model(qc), changes),
      read: () => qc.fetchQuery({ queryKey: READ_KEY(taskId), queryFn: () => read(model(qc), taskId), staleTime: 0 }),
      isCurrent: () => {
        const state = qc.getQueryState(READ_KEY(taskId));
        return state?.status === 'success' && state.fetchStatus === 'idle';
      },
      accept: (r) => qc.setQueryData(READ_KEY(taskId), r),
    };
    return actions;
  },
};
