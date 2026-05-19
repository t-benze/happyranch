/** Mirror of src/daemon/routes/kb.py */
import { request } from './client';
import type { KBEntry } from './types';

export const listKB = (
  slug: string,
  params?: { topic?: string; type?: string },
): Promise<{ entries: KBEntry[] }> =>
  request(`/orgs/${slug}/kb`, { params });

export const searchKB = (
  slug: string,
  params: { q: string; limit?: number },
): Promise<{ entries: KBEntry[] }> =>
  request(`/orgs/${slug}/kb/search`, { params });

export const getKBEntry = (slug: string, entrySlug: string): Promise<KBEntry> =>
  request(`/orgs/${slug}/kb/${entrySlug}`);

export const addKBEntry = (
  slug: string,
  body: {
    slug: string;
    title: string;
    type: string;
    topic: string;
    body: string;
    agent: string;
    tags?: string[];
    related_entries?: string[];
    source_task?: string;
  },
): Promise<{ slug: string; updated_at: string }> =>
  request(`/orgs/${slug}/kb`, { method: 'POST', body });

export const updateKBEntry = (
  slug: string,
  entrySlug: string,
  body: Record<string, unknown>,
): Promise<{ slug: string; updated_at: string }> =>
  request(`/orgs/${slug}/kb/${entrySlug}`, { method: 'POST', body });

export const deleteKBEntry = (
  slug: string,
  entrySlug: string,
  body: { agent: string; as_founder?: boolean },
): Promise<{ slug: string }> =>
  request(`/orgs/${slug}/kb/${entrySlug}`, { method: 'DELETE', body });

export const reindexKB = (slug: string): Promise<{ ok: true }> =>
  request(`/orgs/${slug}/kb/reindex`, { method: 'POST' });
