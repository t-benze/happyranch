/**
 * Two-pane threads page.
 *
 * Left:  InboxList (subscribes to inbox SSE for invalidation)
 * Right: ThreadDetailPane (header + MessageList + composer footer; tail SSE)
 *
 * Phase 8 wires the read path and SSE. Composer + dialogs land in Phase 9.
 */
import { useNavigate, useParams } from 'react-router-dom';
import { useOrgSlug } from '@/lib/orgSlug';
import { InboxList } from './InboxList';
import { ThreadDetailPane } from './ThreadDetailPane';
import { useThreadsInboxSSE } from './hooks';

export function ThreadsPage(): JSX.Element {
  const slug = useOrgSlug();
  const navigate = useNavigate();
  const { thread_id: threadId } = useParams<{ thread_id: string }>();

  // Subscribe to inbox SSE while the page is mounted.
  useThreadsInboxSSE(slug);

  return (
    <div className="grid h-full grid-cols-[320px_1fr]">
      <InboxList
        onCompose={() => {
          // Phase 9 wires the NewThreadDialog. For now leave a stub.
          navigate(`/orgs/${slug}/threads`);
        }}
      />
      {threadId ? (
        <ThreadDetailPane
          threadId={threadId}
          onInvite={() => {}}
          onArchive={() => {}}
          onAbandon={() => {}}
          onExtend={() => {}}
          footer={
            <p className="text-xs text-fg-muted">
              Composer lands in Phase 9 (this commit ships the read path).
            </p>
          }
        />
      ) : (
        <section className="flex h-full items-center justify-center text-fg-muted">
          <p className="text-sm">Select a thread from the inbox.</p>
        </section>
      )}
    </div>
  );
}
