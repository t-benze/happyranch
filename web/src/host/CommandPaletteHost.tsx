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
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';
// ⌘K hotkey is now owned by AssistantDockHost (design-overhaul v1).
// The pre-overhaul command palette is retained but opened via a different
// trigger; remove its ⌘K hotkey to avoid conflicts.
// import { useCommandPaletteHotkey } from '@/hooks/command-palette';
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

type Translator = (key: MessageKey, params?: MessageParams) => string;

export function buildSections(
  qc: ReturnType<typeof useQueryClient>,
  slug: string | null,
  t: Translator,
): CommandPaletteSection[] {
  const sections: CommandPaletteSection[] = [];

  if (!slug) {
    // No active org — only orgs can be jumped to.
    const orgsData = qc.getQueryData<OrgsListResponse>(['orgs']);
    if (orgsData?.orgs?.length) {
      sections.push({
        label: t('palette.section.orgs'),
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
      label: t('palette.section.threads'),
      items: threads.map((t2) => ({
        key: `thread:${t2.thread_id}`,
        primary: `${t2.thread_id} · ${t2.subject}`,
        href: `/orgs/${slug}/threads/${t2.thread_id}`,
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
      label: t('palette.section.tasks'),
      items: tasks.map((task) => ({
        key: `task:${task.task_id}`,
        primary: `${task.task_id} · ${task.brief}`,
        href: `/orgs/${slug}/tasks/${task.task_id}`,
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
      label: t('palette.section.agents'),
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
      label: t('palette.section.kb'),
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
      label: t('palette.section.orgs'),
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
  const { t } = useTranslation();

  // const toggle = React.useCallback(() => setOpen((o) => !o), []);
  // useCommandPaletteHotkey(toggle); — disabled: ⌘K now opens AssistantDock.

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
      return buildSections(qc, slug, t);
    },
    // `cacheTick` is the live re-render signal — see effect above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [open, qc, slug, cacheTick, t],
  );

  return (
    <CommandPalette
      open={open}
      onClose={() => setOpen(false)}
      sections={sections}
      title={t('palette.title')}
      description={t('palette.description')}
      searchPlaceholder={t('palette.searchPlaceholder')}
      searchLabel={t('palette.searchLabel')}
      resultsLabel={t('palette.resultsLabel')}
      noMatches={t('palette.noMatches')}
      nothingLoaded={t('palette.nothingLoaded')}
      navigateLabel={t('palette.footer.navigate')}
      openLabel={t('palette.footer.open')}
      closeLabel={t('palette.footer.close')}
      onSelect={(href) => {
        setOpen(false);
        navigate(href);
      }}
    />
  );
}
