/** Backend contract for the later Secrets page. Mutations are sent once. */
import { request } from './client';

export interface SecretMetadata { name: string; updated_at: string; revision: number }
export interface SecretMutation { name: string; updated_at?: string; deleted?: true; revision: number; operation_id: string }
export type SecretOutcome = { operation_id: string; outcome: 'unknown' } |
  (SecretMutation & { outcome: 'committed'; action: 'create' | 'replace' | 'delete' });
const path = (slug: string) => `/orgs/${encodeURIComponent(slug)}/secrets`;
export const listOrgSecrets = (slug: string): Promise<{ key_state: 'available' | 'locked'; revision: number; secrets: SecretMetadata[] }> =>
  request(path(slug));
export const checkOrgSecretKey = (slug: string): Promise<{ key_state: 'available' | 'locked'; hold_reason: 'org_secrets_locked' | null }> =>
  request(`${path(slug)}/key-status`, { method: 'POST', replayOnUnauthorized: false });
export const getOrgSecretOutcome = (slug: string, operationId: string): Promise<SecretOutcome> =>
  request(`${path(slug)}/operations/${encodeURIComponent(operationId)}`);
export const createOrgSecret = (slug: string, name: string, value: string, operationId: string): Promise<SecretMutation> =>
  request(path(slug), { method: 'POST', body: { name, value }, headers: { 'X-Secret-Operation': operationId }, replayOnUnauthorized: false });
export const replaceOrgSecret = (slug: string, name: string, value: string, operationId: string): Promise<SecretMutation> =>
  request(`${path(slug)}/${encodeURIComponent(name)}`, { method: 'PUT', body: { value }, headers: { 'X-Secret-Operation': operationId }, replayOnUnauthorized: false });
export const deleteOrgSecret = (slug: string, name: string, operationId: string): Promise<SecretMutation> =>
  request(`${path(slug)}/${encodeURIComponent(name)}`, { method: 'DELETE', headers: { 'X-Secret-Operation': operationId }, replayOnUnauthorized: false });
