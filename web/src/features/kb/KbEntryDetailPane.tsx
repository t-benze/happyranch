import { Link, useNavigate } from 'react-router-dom';
import {
  Drawer,
  DrawerContent,
  DrawerTitle,
} from '@/design-system/primitives/Drawer';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { Markdown } from '@/design-system/patterns/Markdown';
import { useKBEntry, useKbRoutes } from '@/hooks/kb';
import { useTasksRoutes } from '@/hooks/tasks';
import { KB_STRINGS } from './strings';

function relativeAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h`;
  const d = Math.round(hr / 24);
  return `${d}d`;
}

export function KbEntryDetailPane({ entrySlug }: { entrySlug: string }): JSX.Element {
  const navigate = useNavigate();
  const kbRoutes = useKbRoutes();
  const tasksRoutes = useTasksRoutes();
  const entryQuery = useKBEntry(entrySlug);
  const onClose = () => navigate(kbRoutes.inbox());
  const entry = entryQuery.data;

  return (
    <Drawer open onOpenChange={(o) => !o && onClose()}>
      <DrawerContent className="flex flex-col">
        <header className="border-border-subtle border-b p-4">
          <div className="text-fg-muted font-mono text-xs">{entrySlug}</div>
          <DrawerTitle className="text-fg mt-1 text-lg">
            {entry?.title ?? KB_STRINGS.drawerLoading}
          </DrawerTitle>
          {entry && (
            <p className="text-fg-muted mt-1 text-xs">
              {entry.type} · updated {relativeAge(entry.updated_at)} ·{' '}
              {KB_STRINGS.authoredBy(entry.authored_by)}
            </p>
          )}
          {entry && entry.tags.length > 0 && (
            <p className="text-fg-muted mt-1 text-xs">
              {KB_STRINGS.filterTags}: {entry.tags.join(', ')}
            </p>
          )}
        </header>
        <section className="flex-1 overflow-y-auto p-4">
          {entry ? (
            <Markdown body={entry.body} />
          ) : (
            <p className="text-fg-muted text-xs">{KB_STRINGS.drawerLoading}</p>
          )}
          {entry?.source_task && (
            <p className="text-fg-muted mt-6 text-xs">
              {KB_STRINGS.sourceTaskLabel}{' '}
              <IdBadge
                kind="task"
                id={entry.source_task}
                to={tasksRoutes.detail(entry.source_task)}
              />
            </p>
          )}
          {entry && entry.related_entries.length > 0 && (
            <div className="text-fg-muted mt-3 text-xs">
              <div>{KB_STRINGS.relatedEntriesLabel}</div>
              <ul className="mt-1 list-disc pl-5">
                {entry.related_entries.map((slug) => (
                  <li key={slug}>
                    <Link to={kbRoutes.detail(slug)} className="text-accent hover:underline">
                      {slug}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </DrawerContent>
    </Drawer>
  );
}
