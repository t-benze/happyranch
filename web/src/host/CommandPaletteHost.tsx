/**
 * CommandPaletteHost — mounts the Cmd-K palette globally inside AppShell.
 *
 * Per spec `2026-05-19-web-polish-design.md` §6. Owns the open state, wires
 * the hotkey, and gathers indexable rows from React Query caches. By design
 * it does NOT subscribe to the source hooks (no fan-out fetch on every
 * keystroke). Sections show whatever the surrounding session has already
 * loaded into cache.
 */
import { useQueryClient } from '@tanstack/react-query';
import * as React from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import {
  CommandPalette,
  type CommandPaletteSection,
} from '@/design-system/patterns/CommandPalette';
import { useCommandPaletteHotkey } from '@/hooks/command-palette';
import type {
  AgentSummary,
  KBEntry,
  OrgsListResponse,
  TaskRecord,
  ThreadRecord,
} from '@/lib/api/types';

function mergeCacheLists<T>(
  entries: [readonly unknown[], unknown][],
  key: keyof T,
  pluck: (data: unknown) => T[] | undefined,
): T[] {
  const seen = new Set<unknown>();
  const out: T[] = [];
  for (const [, data] of entries) {
    const list = pluck(data);
    if (!list) continue;
    for (const item of list) {
      const dedupeKey = item[key];
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      out.push(item);
    }
  }
  return out;
}

function buildSections(
  qc: ReturnType<typeof useQueryClient>,
  slug: string | null,
): CommandPaletteSection[] {
  const sections: CommandPaletteSection[] = [];

  if (!slug) {
    // No active org — only orgs can be jumped to.
    const orgsData = qc.getQueryData<OrgsListResponse>(['orgs']);
    if (orgsData?.orgs?.length) {
      sections.push({
        label: 'Orgs',
        items: orgsData.orgs.map((o) => ({
          key: `org:${o.slug}`,
          primary: o.slug,
          href: `/orgs/${o.slug}/threads`,
        })),
      });
    }
    return sections;
  }

  // Threads
  const threadEntries = qc.getQueriesData<{ threads: ThreadRecord[] }>({
    queryKey: ['threads', slug],
  });
  const threads = mergeCacheLists<ThreadRecord>(
    threadEntries,
    'thread_id',
    (data) => (data as { threads?: ThreadRecord[] })?.threads,
  );
  if (threads.length) {
    sections.push({
      label: 'Threads',
      items: threads.map((t) => ({
        key: `thread:${t.thread_id}`,
        primary: `${t.thread_id} · ${t.subject}`,
        href: `/orgs/${slug}/threads/${t.thread_id}`,
      })),
    });
  }

  // Tasks
  const taskEntries = qc.getQueriesData<{ tasks: TaskRecord[] }>({
    queryKey: ['tasks', slug],
  });
  const tasks = mergeCacheLists<TaskRecord>(
    taskEntries,
    'task_id',
    (data) => (data as { tasks?: TaskRecord[] })?.tasks,
  );
  if (tasks.length) {
    sections.push({
      label: 'Tasks',
      items: tasks.map((t) => ({
        key: `task:${t.task_id}`,
        primary: `${t.task_id} · ${t.brief}`,
        href: `/orgs/${slug}/tasks/${t.task_id}`,
      })),
    });
  }

  // Agents
  const agentsData = qc.getQueryData<{ agents: AgentSummary[] }>([
    'agents',
    slug,
  ]);
  if (agentsData?.agents?.length) {
    sections.push({
      label: 'Agents',
      items: agentsData.agents.map((a) => ({
        key: `agent:${a.name}`,
        primary: a.name,
        secondary: a.team ?? undefined,
        href: `/orgs/${slug}/agents/${a.name}`,
      })),
    });
  }

  // KB
  const kbEntries = qc.getQueriesData<{ entries: KBEntry[] }>({
    queryKey: ['kb-list', slug],
  });
  const kb = mergeCacheLists<KBEntry>(
    kbEntries,
    'slug',
    (data) => (data as { entries?: KBEntry[] })?.entries,
  );
  if (kb.length) {
    sections.push({
      label: 'KB',
      items: kb.map((e) => ({
        key: `kb:${e.slug}`,
        primary: `${e.slug} · ${e.title}`,
        href: `/orgs/${slug}/kb/${e.slug}`,
      })),
    });
  }

  // Orgs always-last (rarely the founder needs them when an org is active,
  // but keep them reachable for multi-org runtimes).
  const orgsData = qc.getQueryData<OrgsListResponse>(['orgs']);
  if (orgsData?.orgs?.length) {
    sections.push({
      label: 'Orgs',
      items: orgsData.orgs.map((o) => ({
        key: `org:${o.slug}`,
        primary: o.slug,
        href: `/orgs/${o.slug}/threads`,
      })),
    });
  }

  return sections;
}

export function CommandPaletteHost(): JSX.Element {
  const [open, setOpen] = React.useState(false);
  // The QueryClient reference is stable for the whole session, so a
  // `useMemo(..., [qc])` dep wouldn't recompute when underlying queries
  // finish. While the palette is open we subscribe to the QueryCache and
  // bump this counter on every event so the sections snapshot reflects
  // anything that loads after the palette opens (Codex review P2).
  const [cacheTick, setCacheTick] = React.useState(0);
  const { slug = null } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const toggle = React.useCallback(() => setOpen((o) => !o), []);
  useCommandPaletteHotkey(toggle);

  React.useEffect(() => {
    if (!open) return;
    const cache = qc.getQueryCache();
    setCacheTick((t) => t + 1);
    const unsubscribe = cache.subscribe(() => {
      setCacheTick((t) => t + 1);
    });
    return unsubscribe;
  }, [open, qc]);

  const sections = React.useMemo(
    () => {
      if (!open) return [];
      return buildSections(qc, slug);
    },
    // `cacheTick` is the live re-render signal — see effect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, qc, slug, cacheTick],
  );

  return (
    <CommandPalette
      open={open}
      onClose={() => setOpen(false)}
      sections={sections}
      onSelect={(href) => {
        setOpen(false);
        navigate(href);
      }}
    />
  );
}
