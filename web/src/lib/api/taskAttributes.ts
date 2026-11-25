/** Eight org-local attribute operations; no automatic conflict replay or UI. */
import { request } from './client';
import type {
  AttributeChange, AttributeDefinitionFields, AttributeDefinitionRead, AttributeDefinitionsRead,
  TaskAttributeHistoryRead, TaskAttributesRead,
} from './taskAttributeTypes';

const definitions = (slug: string) => `/orgs/${slug}/task-attributes/definitions`;
const attributes = (slug: string, taskId: string) => `/orgs/${slug}/tasks/${encodeURIComponent(taskId)}/attributes`;

export const listDefinitions = (slug: string, params?: { include_archived?: boolean; session_id?: string }):
Promise<AttributeDefinitionsRead> => request(definitions(slug), { params });

export const getDefinition = (slug: string, definitionId: string, params?: { version?: number; session_id?: string }):
Promise<AttributeDefinitionRead> => request(`${definitions(slug)}/${encodeURIComponent(definitionId)}`, { params });

export const createDefinition = (slug: string, body: AttributeDefinitionFields & { expected_version: 0 }):
Promise<AttributeDefinitionRead> => request(definitions(slug), { method: 'POST', body });

export const updateDefinition = (slug: string, definitionId: string,
  body: Partial<AttributeDefinitionFields> & { expected_version: number }): Promise<AttributeDefinitionRead> =>
  request(`${definitions(slug)}/${encodeURIComponent(definitionId)}`, { method: 'PATCH', body });

export const archiveDefinition = (slug: string, definitionId: string, expectedVersion: number):
Promise<AttributeDefinitionRead> => request(`${definitions(slug)}/${encodeURIComponent(definitionId)}/archive`, {
  method: 'POST', body: { expected_version: expectedVersion },
});

export const getAttributes = (slug: string, taskId: string, sessionId?: string): Promise<TaskAttributesRead> =>
  request(attributes(slug, taskId), { params: sessionId === undefined ? {} : { session_id: sessionId } });

export const saveAttributes = (slug: string, taskId: string, changes: AttributeChange[], sessionId?: string):
Promise<TaskAttributesRead> => request(attributes(slug, taskId), {
  method: 'PUT', body: { changes }, params: sessionId === undefined ? {} : { session_id: sessionId },
});

export const getAttributeHistory = (slug: string, taskId: string, params?: { definition_id?: string; session_id?: string }):
Promise<TaskAttributeHistoryRead> => request(`${attributes(slug, taskId)}/history`, { params });
