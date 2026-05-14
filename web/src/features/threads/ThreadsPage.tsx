/**
 * Threads page shell. Phase 7 sets up routing/layout. Phase 8 wires SSE + the
 * inbox list. Phase 9 adds the composer and the dialogs.
 */
import { useOrgSlug } from '@/lib/orgSlug';

export function ThreadsPage(): JSX.Element {
  const slug = useOrgSlug();
  return (
    <div className="grid h-full grid-cols-[320px_1fr]">
      <aside className="border-r border-border bg-bg-subtle p-4">
        <h2 className="text-xs uppercase tracking-wide text-fg-muted">
          Inbox · {slug}
        </h2>
        <p className="mt-4 text-sm text-fg-muted">
          Inbox list lands in Phase 8.
        </p>
      </aside>
      <section className="flex h-full items-center justify-center text-fg-muted">
        <p className="text-sm">Select a thread.</p>
      </section>
    </div>
  );
}
