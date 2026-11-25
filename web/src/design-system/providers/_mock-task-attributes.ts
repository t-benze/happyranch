/** Deterministic prototype model only (THR-390 PR6). These observations are not daemon/DB proof. */
import { useQueries, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/lib/api';
import type { AttributeDefinition, AttributeDefinitionRead } from '@/lib/api/taskAttributeTypes';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import type { TaskAttributeActions, TaskAttributesApi, TaskAttributesQuery } from './DataContext';
import {
  DEFAULT_TASK_ATTRIBUTE_FIXTURE,
  TASK_ATTRIBUTE_FIXTURES,
  type TaskAttributeFixture,
  type TaskAttributeFixtureResponse,
} from './_task-attribute-fixtures';

type Model = TaskAttributeFixture;
const MODEL_KEY = ['mock-task-attributes-model'];

function model(qc: QueryClient): Model {
  const cached = qc.getQueryData<Model>(MODEL_KEY);
  if (cached) return cached;
  const initial = structuredClone(TASK_ATTRIBUTE_FIXTURES[DEFAULT_TASK_ATTRIBUTE_FIXTURE]) as Model;
  qc.setQueryData(MODEL_KEY, initial);
  return initial;
}

function answer(response: TaskAttributeFixtureResponse): AttributeDefinitionRead {
  if (response.status >= 400) {
    const detail = (response.body as { detail?: { code?: string } }).detail;
    throw new ApiError(response.status, detail?.code ?? null, detail ?? null);
  }
  return response.body as AttributeDefinitionRead;
}

function store(qc: QueryClient, m: Model, d: AttributeDefinition): AttributeDefinitionRead {
  const read: AttributeDefinitionRead = {
    definition: d,
    editability: d.archived
      ? { can_create: true, can_edit_metadata: false, can_edit_structure: false, can_archive: false, refusal_reason: { code: 'definition_archived' } }
      : { can_create: true, can_edit_metadata: true, can_edit_structure: d.first_used_at === null, can_archive: true, refusal_reason: null },
  };
  m.definitions[d.definition_id] = read;
  m.list.definitions = [...m.list.definitions.filter((x) => x.definition_id !== d.definition_id), d];
  void qc.invalidateQueries({ queryKey: ['mock-task-attributes'] });
  return read;
}

const shape = <T>(q: { data: T | undefined; isLoading: boolean; isError: boolean; error: Error | null; isSuccess: boolean; isFetching: boolean; dataUpdatedAt: number; refetch: () => Promise<unknown> }): TaskAttributesQuery<T> => ({
  data: q.data, isLoading: q.isLoading, isError: q.isError, error: q.error, isSuccess: q.isSuccess,
  isFetching: q.isFetching, dataUpdatedAt: q.dataUpdatedAt, refetch: q.refetch,
});

function readOrThrow(m: Model, id: string, version?: number): AttributeDefinitionRead {
  if (version !== undefined) {
    const older = m.versions?.[id]?.find((r) => r.definition.version === version);
    if (older) return older;
  }
  const read = m.definitions[id];
  if (!read) throw new ApiError(404, 'not_found', { code: 'not_found' });
  return read;
}

export const mockTaskAttributesApi: TaskAttributesApi = {
  useDefinitionList: () => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    return shape(useQuery({ queryKey: ['mock-task-attributes', slug, 'list'], queryFn: async () => structuredClone(model(qc).list), enabled: !!slug }));
  },
  useDefinitionReads: (ids, enabled) => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    const results = useQueries({ queries: ids.map((id) => ({
      queryKey: ['mock-task-attributes', slug, 'definition', id],
      queryFn: async () => structuredClone(readOrThrow(model(qc), id)),
      enabled: enabled && !!slug,
    })) });
    return Object.fromEntries(ids.map((id, i) => [id, shape(results[i] as Parameters<typeof shape<AttributeDefinitionRead>>[0])]));
  },
  useDefinition: (definitionId) => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    return shape(useQuery({
      queryKey: ['mock-task-attributes', slug, 'definition', definitionId ?? ''],
      queryFn: async () => structuredClone(readOrThrow(model(qc), definitionId ?? '')),
      enabled: !!slug && !!definitionId,
      refetchOnMount: 'always',
    }));
  },
  useDefinitionVersions: (definitionId, versions, enabled) => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    return shape(useQuery({
      queryKey: ['mock-task-attributes', slug, 'versions', definitionId, versions.join(',')],
      queryFn: async () => versions.map((v) => structuredClone(readOrThrow(model(qc), definitionId, v))),
      enabled: enabled && !!slug && versions.length > 0,
    }));
  },
  useTaskAttributeActions: () => {
    const slug = useOrgSlugOptional() ?? '';
    const qc = useQueryClient();
    const actions: TaskAttributeActions = {
      create: async (body) => {
        const m = model(qc);
        if (m.writes?.create) return answer(m.writes.create);
        const now = new Date().toISOString();
        return store(qc, m, {
          definition_id: `def_mock_${m.list.definitions.length + 1}`, version: 1, key: body.key, type: body.type,
          name: body.name.trim(), description: body.description ?? null, constraints: body.constraints ?? {},
          created_at: now, recorded_at: now, first_used_at: null, archived_at: null, archived: false,
        });
      },
      update: async (id, body) => {
        const m = model(qc);
        if (m.writes?.update) return answer(m.writes.update);
        const current = readOrThrow(m, id).definition;
        const { expected_version: _expected, ...fields } = body;
        return store(qc, m, { ...current, ...fields, name: (fields.name ?? current.name).trim(), version: current.version + 1, recorded_at: new Date().toISOString() });
      },
      archive: async (id) => {
        const m = model(qc);
        if (m.writes?.archive) return answer(m.writes.archive);
        const current = readOrThrow(m, id).definition;
        const now = new Date().toISOString();
        return store(qc, m, { ...current, archived: true, archived_at: now, version: current.version + 1, recorded_at: now });
      },
      readList: async () => structuredClone(model(qc).list),
      readDefinition: async (id) => structuredClone(readOrThrow(model(qc), id)),
      isDefinitionCurrent: (id) => {
        const state = qc.getQueryState(['mock-task-attributes', slug, 'definition', id]);
        return state?.status === 'success' && state.fetchStatus === 'idle';
      },
      refreshList: () => qc.invalidateQueries({ queryKey: ['mock-task-attributes', slug] }),
    };
    return actions;
  },
};
