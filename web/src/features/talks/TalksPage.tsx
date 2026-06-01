/**
 * Two-pane talks composition. Polls every 60s; no SSE.
 */
import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { ThreadsLayout } from '@/design-system/layouts/ThreadsLayout';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import type { TalkRecord } from '@/lib/api/types';
import { isGPrefixArmed } from '@/hooks/global-jump';
import { useTasksRoutes } from '@/hooks/tasks';
import { useTalk, useTalksList, useTalksRoutes } from '@/hooks/talks';
import { AbandonTalkDialog } from './AbandonTalkDialog';
import { DispatchFromTalkDialog } from './DispatchFromTalkDialog';
import { EndTalkDialog } from './EndTalkDialog';
import { StartTalkDialog } from './StartTalkDialog';
import { TalkTranscript } from './TalkTranscript';

const STATUS_TABS = ['open', 'closed', 'abandoned'] as const;
type StatusTab = (typeof STATUS_TABS)[number];

/** Map talk status onto InboxRow's threads-style status enum.
 * InboxRow's `status` was narrowed to `'open' | 'archived'` after the
 * thread close-out removal; talks' terminal states (closed + abandoned)
 * both map to `'archived'` for visual consistency. */
function inboxStatus(s: TalkRecord['status']): 'open' | 'archived' {
  if (s === 'open') return 'open';
  return 'archived';
}

export function TalksPage(): JSX.Element {
  const routes = useTalksRoutes();
  const tasksRoutes = useTasksRoutes();
  const navigate = useNavigate();
  const { talk_id: talkId } = useParams<{ talk_id: string }>();

  const [status, setStatus] = useState<StatusTab>('open');
  const [filter, setFilter] = useState('');
  const [showStart, setShowStart] = useState(false);
  const [showAbandon, setShowAbandon] = useState(false);
  const [showEnd, setShowEnd] = useState(false);
  const [showDispatch, setShowDispatch] = useState(false);

  const listQuery = useTalksList({ status });
  const talks = useMemo(() => {
    const all = listQuery.data?.talks ?? [];
    if (!filter.trim()) return all;
    const needle = filter.toLowerCase();
    return all.filter(
      (t) =>
        t.agent_name.toLowerCase().includes(needle) ||
        t.talk_id.toLowerCase().includes(needle),
    );
  }, [listQuery.data, filter]);

  const detail = useTalk(talkId);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;
      // Don't let `g d` / `g l` chords also fire the bare D/N actions here.
      if (isGPrefixArmed()) return;
      if (e.key === 'n' || e.key === 'N') {
        e.preventDefault();
        setShowStart(true);
      } else if (talkId && detail.data?.status === 'open' && (e.key === 'e' || e.key === 'E')) {
        e.preventDefault();
        setShowEnd(true);
      } else if (talkId && detail.data?.status === 'open' && (e.key === 'x' || e.key === 'X')) {
        e.preventDefault();
        setShowAbandon(true);
      } else if (talkId && detail.data?.status === 'open' && (e.key === 'd' || e.key === 'D')) {
        e.preventDefault();
        setShowDispatch(true);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [talkId, detail.data?.status]);

  return (
    <>
      <ThreadsLayout
        inbox={(
        <aside className="border-border-default bg-surface-sunken flex h-full flex-col border-r">
          <header className="border-border-default border-b px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-overline text-text-muted tracking-wide uppercase">
                Talks
              </h2>
              <Button
                size="sm"
                onClick={() => setShowStart(true)}
                title="Start (N)"
                aria-label="Start new talk"
              >
                + Start
              </Button>
            </div>
            <Input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter…"
              className="text-caption mt-2 h-7 px-2 py-1"
              aria-label="Filter talks"
            />
            <Tabs
              className="mt-2"
              value={status}
              onValueChange={(v) => setStatus(v as StatusTab)}
              aria-label="Status filter"
            >
              <TabsList>
                {STATUS_TABS.map((s) => (
                  <TabsTrigger key={s} value={s}>{s}</TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </header>
          <div className="flex-1 overflow-auto p-2">
            {listQuery.isLoading && (
              <p className="text-caption text-text-muted px-2 py-4">Loading…</p>
            )}
            {listQuery.isError && (
              <p className="text-caption text-feedback-danger px-2 py-4">
                Failed to load talks.
              </p>
            )}
            {!listQuery.isLoading && talks.length === 0 && (
              <p className="text-caption text-text-muted px-2 py-4">
                {filter
                  ? 'No talks match the filter.'
                  : 'No talks yet. Press N to start.'}
              </p>
            )}
            <div className="flex flex-col gap-1">
              {talks.map((t) => {
                const path = routes.detail(t.talk_id);
                const dateIso = t.ended_at ?? t.started_at;
                const date = dateIso.slice(0, 10);
                return (
                  <InboxRow
                    key={t.talk_id}
                    threadId={t.talk_id}
                    subject={t.agent_name}
                    status={inboxStatus(t.status)}
                    needsYou={false}
                    active={t.talk_id === talkId}
                    meta={t.ended_at ? `closed ${date}` : `started ${date}`}
                    href={path}
                    onSelect={() => navigate(path)}
                  />
                );
              })}
            </div>
          </div>
        </aside>
        )}
        detail={(
        <main className="flex h-full flex-1 flex-col">
          {!talkId && (
            <EmptyState
              title="Select a talk"
              body="Select a talk from the inbox, or press N to start a new one."
            />
          )}
          {talkId && detail.isLoading && (
            <div className="text-text-muted flex h-full items-center justify-center">
              <p>Loading…</p>
            </div>
          )}
          {talkId && detail.isError && (
            <div className="text-feedback-danger flex h-full items-center justify-center">
              <p>Failed to load talk.</p>
            </div>
          )}
          {talkId && detail.data && (
            <>
              <header className="border-border-default flex items-center justify-between gap-3 border-b px-4 py-2">
                <div>
                  <div className="text-overline text-text-muted tracking-wide uppercase">
                    {detail.data.talk_id} · {detail.data.agent_name} · {detail.data.status}
                  </div>
                  {detail.data.summary && (
                    <div className="text-body text-text mt-1">{detail.data.summary}</div>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={detail.data.status !== 'open'}
                    title="Dispatch (D)"
                    onClick={() => setShowDispatch(true)}
                  >
                    Dispatch
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={detail.data.status !== 'open'}
                    title="End (E)"
                    onClick={() => setShowEnd(true)}
                  >
                    End
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={detail.data.status !== 'open'}
                    title="Abandon (X)"
                    onClick={() => setShowAbandon(true)}
                  >
                    Abandon
                  </Button>
                </div>
              </header>
              <section className="flex-1 overflow-hidden">
                {detail.data.status === 'closed' && detail.data.transcript ? (
                  <TalkTranscript
                    transcript={detail.data.transcript}
                    agentName={detail.data.agent_name}
                    timestamp={detail.data.started_at}
                  />
                ) : detail.data.status === 'closed' && detail.data.transcript_path ? (
                  <div className="text-text-muted flex h-full items-center justify-center">
                    <p className="text-body">
                      Transcript stored at <code>{detail.data.transcript_path}</code>.
                    </p>
                  </div>
                ) : detail.data.status === 'open' ? (
                  <EmptyState
                    title="Talk is open"
                    body="Use `happyranch talk resume` to converse, then End to record the transcript."
                  />
                ) : (
                  <EmptyState
                    title={`Talk ${detail.data.status}`}
                    body="No transcript was recorded for this talk."
                  />
                )}
              </section>
            </>
          )}
        </main>
        )}
      />

      <StartTalkDialog
        open={showStart}
        onClose={() => setShowStart(false)}
        onStarted={(newId) => navigate(routes.detail(newId))}
      />
      {talkId && (
        <>
          <AbandonTalkDialog
            talkId={talkId}
            open={showAbandon}
            onClose={() => setShowAbandon(false)}
          />
          <EndTalkDialog
            talkId={talkId}
            open={showEnd}
            onClose={() => setShowEnd(false)}
          />
          <DispatchFromTalkDialog
            talkId={talkId}
            open={showDispatch}
            onClose={() => setShowDispatch(false)}
            onDispatched={(taskId) => navigate(tasksRoutes.detail(taskId))}
          />
        </>
      )}
    </>
  );
}
