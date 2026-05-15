/**
 * Two-pane threads page.
 *
 * Left:  InboxList (subscribes to inbox SSE for invalidation)
 * Right: ThreadDetailPane (header + MessageList + Composer; tail SSE)
 *
 * Dialogs (NewThread / Invite / Archive / Abandon / Extend) live as siblings
 * and are driven by local UI state.
 */
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useOrgSlug } from '@/lib/orgSlug';
import { AbandonDialog } from './AbandonDialog';
import { ArchiveDialog } from './ArchiveDialog';
import { Composer } from './Composer';
import { ExtendDialog } from './ExtendDialog';
import { HelpDrawer } from './HelpDrawer';
import { InboxList } from './InboxList';
import { InviteDialog } from './InviteDialog';
import { NewThreadDialog } from './NewThreadDialog';
import { ThreadDetailPane } from './ThreadDetailPane';
import { useThread, useThreadMessages, useThreadsInboxSSE } from './hooks';

export function ThreadsPage(): JSX.Element {
  const slug = useOrgSlug();
  const navigate = useNavigate();
  const { thread_id: threadId } = useParams<{ thread_id: string }>();
  const composerFocusRef = useRef<(() => void) | null>(null);

  useThreadsInboxSSE(slug);

  const [showNew, setShowNew] = useState(false);
  const [newPrefill, setNewPrefill] = useState<
    | { subject?: string; recipients?: string[]; body?: string; forwarded_from_id?: string; forwarded_from_kind?: 'thread' | 'talk' }
    | undefined
  >(undefined);
  const [showInvite, setShowInvite] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [showAbandon, setShowAbandon] = useState(false);
  const [showExtend, setShowExtend] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  // Fetch the active thread so dialog handlers know the turn_cap etc.
  const activeThread = useThread(slug, threadId);
  const activeMessages = useThreadMessages(slug, threadId);

  const openNew = () => {
    setNewPrefill(undefined);
    setShowNew(true);
  };

  const openForward = () => {
    if (!threadId || !activeThread.data) return;
    const lastFounderMsg = (activeMessages.data?.messages ?? activeThread.data.messages)
      .filter((m) => m.kind === 'message' && m.body_markdown)
      .at(-1);
    const quoted = lastFounderMsg
      ? `> from ${threadId} by ${lastFounderMsg.speaker}\n>\n` +
        lastFounderMsg.body_markdown!
          .split('\n')
          .map((l) => `> ${l}`)
          .join('\n')
      : `> from ${threadId}`;
    setNewPrefill({
      subject: `Fwd: ${activeThread.data.subject}`,
      body: `${quoted}\n\n`,
      forwarded_from_id: threadId,
      forwarded_from_kind: 'thread',
    });
    setShowNew(true);
  };

  // Keyboard shortcuts: N / I / A / X / R / ?. Limited to when no input is focused.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openNew(); }
      else if (threadId && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); setShowInvite(true); }
      else if (threadId && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); setShowArchive(true); }
      else if (threadId && (e.key === 'x' || e.key === 'X')) { e.preventDefault(); setShowAbandon(true); }
      else if (threadId && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openForward(); }
      else if (threadId && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        composerFocusRef.current?.();
      }
      else if (e.key === '?') { e.preventDefault(); setShowHelp(true); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, activeThread.data, activeMessages.data]);

  return (
    <div className="grid h-full grid-cols-[320px_1fr] grid-rows-[minmax(0,1fr)]">
      <InboxList onCompose={openNew} />
      {threadId ? (
        <ThreadDetailPane
          threadId={threadId}
          onInvite={() => setShowInvite(true)}
          onArchive={() => setShowArchive(true)}
          onAbandon={() => setShowAbandon(true)}
          onExtend={() => setShowExtend(true)}
          footer={
            <Composer
              threadId={threadId}
              disabled={activeThread.data?.status !== 'open'}
              registerFocus={(focus) => { composerFocusRef.current = focus; }}
            />
          }
        />
      ) : (
        <section className="flex h-full items-center justify-center text-fg-muted">
          <p className="text-sm">Select a thread from the inbox. Press <kbd className="rounded border border-border bg-bg-raised px-1.5 py-0.5 text-xs">N</kbd> to compose.</p>
        </section>
      )}

      <NewThreadDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        prefill={newPrefill}
        onCreated={(newId) => navigate(`/orgs/${slug}/threads/${newId}`)}
      />
      <HelpDrawer open={showHelp} onClose={() => setShowHelp(false)} />
      {threadId && (
        <>
          <InviteDialog
            threadId={threadId}
            open={showInvite}
            onClose={() => setShowInvite(false)}
          />
          <ArchiveDialog
            threadId={threadId}
            open={showArchive}
            onClose={() => setShowArchive(false)}
          />
          <AbandonDialog
            threadId={threadId}
            open={showAbandon}
            onClose={() => setShowAbandon(false)}
          />
          <ExtendDialog
            threadId={threadId}
            currentCap={activeThread.data?.turn_cap ?? 500}
            open={showExtend}
            onClose={() => setShowExtend(false)}
          />
        </>
      )}
    </div>
  );
}
