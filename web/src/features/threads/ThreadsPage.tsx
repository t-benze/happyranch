import { identityInitials, useFounderName, useIdentityOptions, useKnownAgentIds, usePreflightAddresses, namingAddressError } from '@/hooks/identities';
import { IdentityName } from '@/shared/identities/IdentityName';
/**
 * ThreadsPage — list + detail reshape (§4.2, design-overhaul).
 *
 * LIST: each row shows LAST SPEAKER, STATUS PILL,
 * and a crescent-moon marker for dream-originated threads.
 *
 * DETAIL: turn cards; SYSTEM CARDS visually distinct from agent-turn
 * cards. In-thread agent-own "ran:" cards are OMITTED (P1 — D7 deferred).
 *
 * States: Loading (skeleton), Empty (calm), Error-with-retry (§2.5.5).
 * NO persistent unread/read-state (B.3 deferred — no markRead/unread).
 *
 * Composer: BROADCAST-ONLY ("Message the thread · @ to address an agent";
 *   hint "Every participant sees messages in this thread.").
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Archive, CircleCheck, CircleX, Info, RotateCcw, Send, TriangleAlert, UserMinus, UserPlus, type LucideIcon } from 'lucide-react';
import { Avatar } from '@/design-system/primitives/Avatar';
import { Button } from '@/design-system/primitives/Button';
import { PageTitle } from '@/design-system/patterns/PageTitle';
import { TopbarActions } from '@/design-system/layouts/AppShell/TopbarSlots';
import { Input } from '@/design-system/primitives/Input';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { Composer } from '@/design-system/patterns/Composer';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import { MessageBubble, type MessageVariant } from '@/design-system/patterns/MessageBubble';
import { StatValue } from '@/design-system/patterns/StatValue';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { StatusBadge, STATUS_TONE, type TaskStatus } from '@/design-system/patterns/StatusBadge';
import { ThreadHeader } from '@/design-system/patterns/ThreadHeader';
import { ContentWrap } from '@/design-system/layouts/ContentWrap/ContentWrap';
import { ApiError, artifacts as artifactsApi } from '@/lib/api';
import type {
  ReplyDeliveryEntry,
  ThreadAttachment,
  ThreadAttachmentRef,
  ThreadGuest,
  ThreadMessage,
} from '@/lib/api/types';
import { allocateArtifactName, attachmentContentType } from '@/lib/threadAttachments';
import type { PendingAttachment } from '@/design-system/patterns/Composer';
import { useAgentsList } from '@/hooks/agents';
import { useThreadFreshTokens } from '@/hooks/tokens';
import {
  useAbortReplies,
  useReplaceGuestCode,
  useRevokeGuest,
  useRenameThread,
  useSendFollowUp,
  useSetThreadPinned,
  useThread,
  useThreadMessages,
  useThreadRoutes,
  useThreadTailSSE,
  useThreadTasks,
  useThreadsInboxSSE,
  useThreadsInfiniteList,
  type ThreadGuestInviteResponse,
} from '@/hooks/threads';
import { ArchiveDialog } from './ArchiveDialog';
import { InviteDialog } from './InviteDialog';
import { GuestInviteDialog } from './GuestInviteDialog';
import { GuestSeats } from './GuestSeats';
import {
  guestEndCauseLabel,
  guestErrorKey,
  guestErrorText,
  guestLabelMap,
  guestSpeakerLabel,
  guestTime,
  isGuestSpeaker,
  type GuestErrorKey,
} from './guestSeats';
import { useRemoteHost } from '@/lib/remotePolicy';
import { RemoveParticipantDialog } from './RemoveParticipantDialog';
import { NewThreadDialog } from '@/shared/threads/NewThreadDialog';
import { ResponderStatusStrip } from './ResponderStatusStrip';
import { ReplyDeliveryStrip, replyDeliveryCaption } from './ReplyDeliveryStrip';
import { ResumeButton } from './ResumeButton';
import { selectInFlightResponders } from './inFlightResponders';
import { TypingBubble } from '@/design-system/patterns/TypingBubble';
import { useTranslation } from '@/hooks/i18n';
import { formatCountFor, type Locale, type MessageKey, type MessageParams } from '@/lib/i18n';
import { formatElapsed } from '@/lib/elapsed';
import {
  classifyThreadError,
  renderThreadError,
  type ThreadErrorDetail,
  type ThreadErrorView,
} from '@/lib/threadErrors';
import { reorderOpenThreads } from '@/design-system/providers/_real-threads';
/* ------------------------------------------------------------------ */
/*  helpers                                                            */
/* ------------------------------------------------------------------ */

/**
 * Shared Artifacts upload refusals (422 `artifact_metadata_*`, quota
 * `artifact_quota_exceeded`) carry an explanatory body; show it verbatim
 * instead of the bare `HTTP <status>` fallback. Other errors return null.
 */
function artifactRefusalDetail(err: unknown): ThreadErrorDetail | null {
  if (!(err instanceof ApiError) || !err.code) return null;
  if (!err.code.startsWith('artifact_metadata_') && err.code !== 'artifact_quota_exceeded') return null;
  return { kind: 'raw', text: `HTTP ${err.status}: ${JSON.stringify(err.detail)}` };
}

function useNowMs(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

/**
 * Host-local calendar-day key ('YYYY-M-D') that advances at local midnight
 * without any reply activity (THR-376 PR2 review R1). One timer is armed for
 * the next local midnight and re-armed after it fires; a tab returning from
 * the background/suspend re-checks immediately. No polling, no requests.
 */
function useLocalDayKey(): string {
  const today = () => {
    const d = new Date();
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  };
  const [key, setKey] = useState(today);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const arm = () => {
      const now = new Date();
      const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
      // +50ms so the callback lands strictly after the local day boundary.
      timer = setTimeout(() => {
        setKey(today());
        arm();
      }, next.getTime() - now.getTime() + 50);
    };
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      setKey(today());
      if (timer !== undefined) clearTimeout(timer);
      arm();
    };
    arm();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      if (timer !== undefined) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);
  return key;
}

// Segmented inbox filter (THREADS-02). 'done' = archived; 'all' = both, derived
// client-side by merging the two per-status fetches (the list payload carries no
// finer waiting/active split, so no other bucket is honestly derivable here).
// THR-099 Batch 2: the 'done' bucket's user-facing LABEL is "Archived" (design
// status vocabulary); the internal key stays 'done' to avoid churn.
type InboxBucket = 'all' | 'open' | 'done';
const INBOX_BUCKETS: InboxBucket[] = ['all', 'open', 'done'];
const BUCKET_LABEL_KEY: Record<InboxBucket, MessageKey> = {
  all: 'threads.page.bucket.all',
  open: 'threads.page.bucket.open',
  done: 'threads.page.bucket.done',
};

/** Translator shape shared by the pure helpers below (THR-118 W3a). */
type Translate = (key: MessageKey, params?: MessageParams) => string;
type RenderTranslated = (key: MessageKey, params?: Record<string, React.ReactNode>) => React.ReactNode;

function threadStatusOrFallback(status: string): 'open' | 'archived' {
  if (status === 'open' || status === 'archived') return status;
  return 'open';
}

/**
 * Relative age label for a thread's start time (THR-061 a-threads `.t-time`,
 * e.g. "1d ago"). Mirrors the local relativeAge pattern already used by
 * TaskCard / DashboardPage — no date library added. Pure over an injected
 * `nowMs` so it stays testable. Returns "just now" under a minute (no "ago").
 * `started_at` is always present on the thread-LIST payload (ThreadRecord);
 * Per-row participant names come from the bounded list projection; previews
 * and counts remain omitted because the list payload does not back them.
 */
function relativeStartLabel(iso: string, nowMs: number, t: Translate): string {
  const min = Math.round((nowMs - new Date(iso).getTime()) / 60000);
  if (min < 1) return t('threads.page.time.justNow');
  if (min < 60) return t('threads.page.time.minutesAgo', { count: min });
  const hr = Math.round(min / 60);
  if (hr < 24) return t('threads.page.time.hoursAgo', { count: hr });
  const d = Math.round(hr / 24);
  return t('threads.page.time.daysAgo', { count: d });
}

/** Display label for a machine responder status (the value itself never changes). */
const RESPONDER_STATUS_KEY: Record<string, MessageKey> = {
  queued: 'threads.page.responder.queued',
  working: 'threads.page.responder.working',
  replied: 'threads.page.responder.replied',
  declined: 'threads.page.responder.declined',
  failed: 'threads.page.responder.failed',
};

function responderStatusLabel(status: string, t: Translate): string {
  const key = RESPONDER_STATUS_KEY[status];
  // An unknown future status renders verbatim (never a guessed translation).
  return key ? t(key) : status;
}

/** Explicit-locale timestamp display (host timezone, as before). */
function localeTimestamp(locale: Locale, iso: string): string {
  return new Date(iso).toLocaleString(locale);
}

/**
 * Transcript message time (explicit locale, host timezone). Time only: the
 * THR-376 day separators carry the date. The full `localeTimestamp` stays
 * available as the hover title.
 */
function messageTime(locale: Locale, iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
}

/**
 * Derive a display label for the last speaker.
 * Returns { name, role } for AgentChip, or null if the thread has no speaker yet.
 */
function lastSpeakerChip(speaker: string | null | undefined, t: Translate): { name: string; label?: React.ReactNode; role: 'manager' | 'worker' | 'founder' } | null {
  if (!speaker) return null;
  if (speaker === 'founder') return { name: 'founder', label: <IdentityName canonicalId="founder" founderNameOnly />, role: 'founder' };
  if (speaker === 'system') return { name: 'system', role: 'worker' };
  // Desktop-app guest (THR-362 §5.4): list rows carry no seat labels, so name
  // the seat id as a guest instead of looking it up as an agent identity.
  if (isGuestSpeaker(speaker)) {
    return { name: speaker, label: t('threads.page.guest.listSpeaker', { id: speaker.slice('guest:'.length) }), role: 'worker' };
  }
  return { name: speaker, label: <IdentityName canonicalId={speaker} />, role: 'worker' };
}

/**
 * Role for a participant avatar chip. Participants are bare name strings, so
 * only the founder is distinguishable; every other agent renders as a worker —
 * mirrors lastSpeakerChip's honest, no-fabrication mapping (the dot is decor).
 */
function participantChipRole(name: string): 'worker' | 'founder' {
  return name === 'founder' ? 'founder' : 'worker';
}

/**
 * LED dot color for a participant row (THR-061 a-thread-detail .pled). The
 * founder keeps their identity color; every other agent's dot reflects their
 * LATEST responder state (honest — the same responder_status the strip shows):
 * replied→accent green, working→info blue, declined→attention amber,
 * failed→danger red. Agents with no recorded state fall back to the neutral
 * muted-text colour, never bg-agent-worker (= accent, the 'replied' colour).
 */
function participantLed(name: string, status: string | null): string {
  if (name === 'founder') return 'bg-agent-founder';
  switch (status) {
    case 'working':
      return 'bg-info';
    case 'replied':
      return 'bg-accent-default';
    case 'declined':
      return 'bg-attention';
    case 'failed':
      return 'bg-danger';
    default:
      return 'bg-text-muted';
  }
}

// Linked-task statuses that count as ACTIVE (sorted to the top of the rail so
// the visible head of the list matches the a-thread-detail chips, which show
// active work first). Everything else is terminal/settled.
const ACTIVE_TASK_STATUSES = new Set(['in_progress', 'pending', 'escalated', 'blocked']);

function isActiveTaskStatus(status: string): boolean {
  return ACTIVE_TASK_STATUSES.has(status);
}

// THR-376 rail section pattern (mockup .rail-lbl / .rail-link / .rail-row).
// .rail-lbl: 11px/600 uppercase ink-2 — the shared eyebrow recipe.
const RAIL_LABEL = 'text-eyebrow text-text-secondary mb-2 uppercase';
const RAIL_LINK =
  'border-border-default text-caption flex items-center gap-2 rounded-sm border px-2.5 py-1.75 transition-colors';
const RAIL_ROW = 'text-body-sm flex items-center justify-between gap-2 py-1.25';

/**
 * Linked-task status pill: the shared StatusBadge (status → tone map), so the
 * rail reads the same as Tasks/Jobs. A status outside the known task
 * vocabulary renders verbatim in the neutral tone rather than guessing.
 */
function LinkedTaskStatus({ status }: { status: string }): JSX.Element {
  return status in STATUS_TONE ? (
    <StatusBadge status={status as TaskStatus} />
  ) : (
    <StatusBadge tone="neutral" label={status} />
  );
}

/**
 * Two-letter (max) avatar initials for a message sender. `founder` uses the
 * Founder's current name (the canonical ID when unnamed/unavailable), matching
 * the sidebar account row; agent IDs split on separators and take the first
 * letter of the first two segments (engineering_manager → EM, product_lead →
 * PL). Presentational only — no fabricated identity.
 */
function speakerInitials(name: string, founderName: string): string {
  return identityInitials(name === 'founder' ? founderName : name);
}

/**
 * Latest known responder state per agent across the transcript, keyed by
 * agent_name. Honest enrichment for the participants rail — reads the same
 * responder_status the ResponderStatusStrip renders (no fabrication). Messages
 * are server-ordered oldest→newest, so the last write wins (newest state).
 */
function latestResponderStatusByAgent(messages: ThreadMessage[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const m of messages) {
    for (const s of m.responder_status ?? []) {
      map.set(s.agent_name, s.status);
    }
  }
  return map;
}

/**
 * Per-sender avatar square for a transcript turn (THR-061 a-thread-detail).
 * Role color mirrors AgentChip's honest founder-vs-worker mapping; the initials
 * are decor. `aria-hidden` because the speaker name is announced by the bubble.
 */
function TurnAvatar({ name, guestLabel }: { name: string; guestLabel?: string }): JSX.Element {
  const founderName = useFounderName();
  // Desktop-app guests (THR-362) get their own tone and label-derived
  // initials: a `guest:G-n` id is never an agent.
  if (isGuestSpeaker(name)) {
    return <Avatar initials={identityInitials(guestLabel ?? name.slice('guest:'.length))} tone="guest" size="md" />;
  }
  const tone = participantChipRole(name) === 'founder' ? 'founder' : 'worker';
  return <Avatar initials={speakerInitials(name, founderName)} tone={tone} size="md" />;
}

/**
 * Aggregate the real produced artifacts across a thread's transcript:
 * every message's attachments, deduped by artifact_name (first occurrence
 * wins). Pure presentation over existing ThreadDetailResponse data — no fetch,
 * no fabrication. Guards `attachments` which older payloads may omit.
 */
function collectThreadArtifacts(messages: ThreadMessage[]): ThreadAttachment[] {
  const seen = new Map<string, ThreadAttachment>();
  for (const m of messages) {
    for (const a of m.attachments ?? []) {
      const key = a.thread_attachment_id ?? a.artifact_name;
      if (!seen.has(key)) seen.set(key, a);
    }
  }
  return [...seen.values()];
}

/**
 * THR-209 visible pin-change error banner. aria-live so failures from
 * optimistic mutations are announced; rendered in both the list and detail
 * columns from the same page-level state.
 */
function PinErrorBanner({ message }: { message: string | null }): JSX.Element | null {
  if (!message) return null;
  return (
    <div
      role="alert"
      aria-live="assertive"
      className="border-border-default bg-surface-raised text-feedback-danger border-b px-4 py-2 text-xs"
    >
      {message}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function InboxSkeleton(): JSX.Element {
  return (
    <div className="animate-pulse space-y-2 p-2">
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="space-y-2 rounded-md px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <div className="bg-surface-raised h-4 w-48 rounded-sm" />
            <div className="bg-surface-raised h-4 w-12 rounded-full" />
          </div>
          <div className="flex items-center gap-2">
            <div className="bg-surface-raised h-3 w-16 rounded-sm" />
            <div className="bg-surface-raised h-3 w-20 rounded-sm" />
            <div className="bg-surface-raised ml-auto h-3 w-12 rounded-sm" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function ThreadsPage(): JSX.Element {
  const { t, locale } = useTranslation();
  const routes = useThreadRoutes();
  const navigate = useNavigate();
  const { slug, thread_id: threadId } = useParams<{ slug: string; thread_id: string }>();
  const listScrollRef = useRef<HTMLDivElement | null>(null);
  const listSentinelRef = useRef<HTMLDivElement | null>(null);
  const refreshAnchorRef = useRef<{ scope: string; href: string; offset: number } | null>(null);
  const listScrollByScopeRef = useRef(new Map<string, number>());
  const restoredListScopeRef = useRef<string | null>(null);
  const rowNavigationSavedScopeRef = useRef<string | null>(null);
  // Inbox state — segmented status filter (THREADS-02).
  const [bucket, setBucket] = useState<InboxBucket>('open');
  const [filter, setFilter] = useState('');
  const scrollKey = `threads:list-scroll:${slug ?? ''}:${bucket}:${filter}`;
  const setListScrollRef = useCallback((node: HTMLDivElement | null) => {
    listScrollRef.current = node;
  }, []);
  const rememberListScroll = (owner = listScrollRef.current) => {
    if (restoredListScopeRef.current !== scrollKey) return;
    // Route-local, keyed state avoids leaking a position across orgs, buckets,
    // or search terms while leaving router ownership untouched. The route
    const scrollTop = owner ? owner.scrollTop : (listScrollByScopeRef.current.get(scrollKey) ?? 0);
    listScrollByScopeRef.current.set(scrollKey, scrollTop);
    sessionStorage.setItem(scrollKey, String(scrollTop));
    sessionStorage.setItem(`${scrollKey}:depth`, String(listQuery.data?.pages.length ?? 1));
  };
  const rememberListScrollSnapshot = () => {
    // Scope cleanup can run after the descendant has rendered a new scope and
    // browser layout has clamped its reused node. Prefer a nonzero observed
    // position, which is authoritative for a real scroll event. A DOM-assisted
    // initial setup has no scroll event, however, so take one connected-owner
    // snapshot at the scope boundary rather than silently replacing it with
    // the map's initial zero.
    const observed = listScrollByScopeRef.current.get(scrollKey);
    const owner = listScrollRef.current;
    const scrollTop = observed ?? (owner?.isConnected ? owner.scrollTop : 0);
    listScrollByScopeRef.current.set(scrollKey, scrollTop);
    sessionStorage.setItem(scrollKey, String(scrollTop));
  };
  const observeListScroll = (event: React.UIEvent<HTMLDivElement>) => {
    if (restoredListScopeRef.current !== scrollKey) return;
    const scrollTop = event.currentTarget.scrollTop;
    listScrollByScopeRef.current.set(scrollKey, scrollTop);
    // Record real user movement while this scope owns the node. A later scope
    // cleanup must never reinterpret a browser-clamped descendant as this
    // scope's last position.
    sessionStorage.setItem(scrollKey, String(scrollTop));
  };
  const rememberRowNavigationScroll = (id: string) => {
    // A row activation observes the attached owner before navigation. Its
    // subsequent route cleanup must not reread a detached/clamped node and
    // overwrite this authoritative snapshot.
    rememberListScroll();
    sessionStorage.setItem(`${scrollKey}:anchor`, id);
    rowNavigationSavedScopeRef.current = scrollKey;
  };
  useEffect(() => {
    const captureReadyOrgScope = () => {
      // Sidebar changes retain this route instance. Capture while the outgoing
      // ready scope still owns the connected list before navigation renders an
      // uncached org's loading content into the same owner. A pending scope has
      // not restored yet, so its durable value must remain untouched.
      if (restoredListScopeRef.current === scrollKey) rememberListScroll();
    };
    window.addEventListener('threads:before-org-change', captureReadyOrgScope);
    return () => window.removeEventListener('threads:before-org-change', captureReadyOrgScope);
  }, [scrollKey]);
  const changeBucket = (nextBucket: InboxBucket) => {
    // Unlike a row activation, a scope control reuses the list owner. Snapshot
    // it before changing state, while it still represents the outgoing scope.
    // This must not depend on a synthetic or browser-delivered scroll event.
    rememberListScroll();
    setBucket(nextBucket);
  };
  const changeFilter = (nextFilter: string) => {
    // Filter changes are scope transitions too; snapshot the outgoing key
    // before the reused owner is reset for the incoming filter.
    rememberListScroll();
    setFilter(nextFilter);
  };
  useThreadsInboxSSE();
  const agentsQuery = useAgentsList();
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
  const listQuery = useThreadsInfiniteList(bucket === 'done' ? 'archived' : bucket === 'open' ? 'open' : undefined);
  const totals = listQuery.data?.pages.at(-1)?.totals;
  const counts = { all: totals?.all, open: totals?.open, done: totals?.archived };
  const dreamOpenedCount = totals?.dream_origin;
  const bucketLoading = listQuery.isLoading;
  const bucketError = listQuery.isError;
  const threads = useMemo(() => {
    // A live cursor can encounter the same ID after an external rename/pin.
    // Later accepted pages own its projection; page metadata stays untouched.
    const latest = new Map(
      (listQuery.data?.pages.flatMap((page) => page.threads) ?? []).map((row) => [row.thread_id, row] as const),
    );
    const rows = [...latest.values()];
    const base = bucket === 'open' ? reorderOpenThreads(rows) : rows.sort((a, b) => {
      const aStamp = bucket === 'done' ? a.archived_at ?? a.started_at : a.started_at;
      const bStamp = bucket === 'done' ? b.archived_at ?? b.started_at : b.started_at;
      if (aStamp !== bStamp) return aStamp > bStamp ? -1 : 1;
      return a.thread_id === b.thread_id ? 0 : a.thread_id > b.thread_id ? -1 : 1;
    });
    if (!filter.trim()) return base;
    const needle = filter.toLowerCase();
    return base.filter(
      (t) =>
        t.subject.toLowerCase().includes(needle) ||
        t.thread_id.toLowerCase().includes(needle),
    );
  }, [listQuery.data, filter, bucket]);
  useLayoutEffect(() => {
    const owner = listScrollRef.current;
    if (!owner || threadId) return;
    if (refreshAnchorRef.current?.scope !== scrollKey) refreshAnchorRef.current = null;
    if (listQuery.isRefreshing && !refreshAnchorRef.current) {
      const top = owner.getBoundingClientRect().top;
      const row = [...owner.querySelectorAll<HTMLAnchorElement>('a[href]')].find((link) => link.getBoundingClientRect().bottom > top);
      if (row) refreshAnchorRef.current = { scope: scrollKey, href: row.getAttribute('href')!, offset: row.getBoundingClientRect().top - top };
    } else if (!listQuery.isRefreshing && refreshAnchorRef.current) {
      const snapshot = refreshAnchorRef.current;
      refreshAnchorRef.current = null;
      const row = [...owner.querySelectorAll<HTMLAnchorElement>('a[href]')].find((link) => link.getAttribute('href') === snapshot.href);
      if (row && !listQuery.isError) owner.scrollTop += row.getBoundingClientRect().top - owner.getBoundingClientRect().top - snapshot.offset;
    }
  }, [threadId, scrollKey, listQuery.isRefreshing, listQuery.isError, listQuery.data]);
  useEffect(() => {
    const owner = listScrollRef.current;
    if (!owner || threadId) return;
    const cancel = (event: Event) => {
      if (event instanceof KeyboardEvent && !['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) return;
      refreshAnchorRef.current = null;
      if (restoredListScopeRef.current === scrollKey) return;
      restoredListScopeRef.current = scrollKey;
      listScrollByScopeRef.current.set(scrollKey, owner.scrollTop);
      sessionStorage.setItem(scrollKey, String(owner.scrollTop));
      sessionStorage.removeItem(`${scrollKey}:anchor`);
      sessionStorage.setItem(`${scrollKey}:depth`, String(listQuery.data?.pages.length ?? 1));
    };
    owner.addEventListener('wheel', cancel, { passive: true });
    owner.addEventListener('touchstart', cancel, { passive: true });
    owner.addEventListener('keydown', cancel);
    return () => {
      owner.removeEventListener('wheel', cancel);
      owner.removeEventListener('touchstart', cancel);
      owner.removeEventListener('keydown', cancel);
    };
  }, [threadId, scrollKey, listQuery.data]);
  // Literal client search exhausts the active stream even when early matches
  // already fill the viewport. Each render advances at most one owned request.
  useEffect(() => {
    if (!threadId && filter.trim() && listQuery.hasNextPage && !bucketError && !listQuery.isFetchingNextPage && !listQuery.isRefreshing) {
      void listQuery.fetchNextPage();
    }
  }, [threadId, filter, bucketError, listQuery]);
  useEffect(() => {
    if (threadId || bucketLoading || bucketError || !listQuery.hasNextPage || listQuery.isRefreshing || !listSentinelRef.current || !listScrollRef.current) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void listQuery.fetchNextPage();
    }, { root: listScrollRef.current });
    observer.observe(listSentinelRef.current);
    return () => observer.disconnect();
  }, [threadId, bucketLoading, bucketError, listQuery]);
  useLayoutEffect(() => {
    // A detail transition ends this list entry. Returning to the list must
    // restore its saved offset, while ordinary query updates inside the entry
    // must leave the live scroll owner alone.
    if (threadId) {
      restoredListScopeRef.current = null;
      rowNavigationSavedScopeRef.current = null;
      return;
    }
    // A scope enters with its ContentWrap already mounted, but restoration
    // waits for the active bucket's list data. This keeps an initial delayed
    // response from losing its route-local saved position and makes refetches
    // (which do not create a new list entry) harmless.
    if (bucketLoading || bucketError || restoredListScopeRef.current === scrollKey) return;
    const stored = sessionStorage.getItem(scrollKey);
    // A missing key must reset the reused scroll owner. A stored zero is a
    // valid saved position, so neither case may be treated as "leave it be".
    const saved = stored === null ? 0 : Number(stored);
    const target = Number.isFinite(saved) && saved >= 0 ? saved : 0;
    const depth = Number(sessionStorage.getItem(`${scrollKey}:depth`) ?? 1);
    const owner = listScrollRef.current;
    const anchor = sessionStorage.getItem(`${scrollKey}:anchor`);
    const enoughDepth = (listQuery.data?.pages.length ?? 0) >= depth;
    const enoughHeight = !owner || owner.clientHeight === 0 || owner.scrollHeight - owner.clientHeight >= target;
    const enoughAnchor = !anchor || threads.some((t) => t.thread_id === anchor);
    if (listQuery.hasNextPage && (!enoughDepth || !enoughHeight || !enoughAnchor)) {
      if (!listQuery.isFetchingNextPage && !listQuery.isRefreshing) void listQuery.fetchNextPage();
      return;
    }
    // This must happen in the layout commit, rather than an animation frame:
    // a returned list has a newly mounted owner and an unavailable frame must
    // not turn a durable position into a no-op.
    if (listScrollRef.current && restoredListScopeRef.current !== scrollKey) {
      listScrollRef.current.scrollTop = target;
      listScrollByScopeRef.current.set(scrollKey, target);
      restoredListScopeRef.current = scrollKey;
    }
  }, [threadId, scrollKey, bucketLoading, bucketError, listQuery, threads]);
  useLayoutEffect(() => {
    // The list ContentWrap is conditional on the detail route. Persist from
    // its cleanup too, so browser history and the detail's ordinary Back link
    // have the same route-local restoration point as an anchor activation.
    // Persist only the position observed while this scope owned the node. A
    // loading entry has not restored a position yet, so it must not overwrite
    // its durable value with the browser's initial/clamped zero on teardown.
    if (threadId) return;
    return () => {
      if (
        restoredListScopeRef.current === scrollKey
        && rowNavigationSavedScopeRef.current !== scrollKey
      ) {
        rememberListScrollSnapshot();
      }
    };
  }, [threadId, scrollKey]);
  // THR-209 msg 9 (TASK-5976): the Pinned section is an OPEN-list concept
  // only. The server returns the open bucket pinned-first, ordered by
  // immutable numeric thread id DESC (THR-10 above THR-2) then unpinned in
  // ordinary order; we split ONLY there for the section header, preserving
  // server order. Archived ('done') and 'all' buckets render ONE flat
  // ordinary list — pin has zero presentation effect there. Rows in every
  // bucket only navigate; pin/unpin mutation controls remain in the detail
  // toolbar.
  const showPinnedSection = bucket === 'open';
  const pinnedThreads = useMemo(
    () => (showPinnedSection ? threads.filter((t) => t.pinned) : []),
    [threads, showPinnedSection],
  );
  const unpinnedThreads = useMemo(
    () => (showPinnedSection ? threads.filter((t) => !t.pinned) : threads),
    [threads, showPinnedSection],
  );

  // Active-thread data
  const activeThread = useThread(threadId);
  const activeMessagesQuery = useThreadMessages(threadId);
  // THR-137: Restrict the thread-detail Composer's @-mention suggestions to
  // only approved agents who are also participants in the currently loaded
  // thread. useMemo recomputes when the thread detail refetches after
  // participant invite/remove invalidation. NewThreadDialog and InviteDialog
  // still receive the full roster (agents), not this filtered set.
  const composerAgents = useMemo(() => {
    if (!activeThread.data?.participants) return [];
    const participantSet = new Set(activeThread.data.participants);
    return agents.filter((a) => participantSet.has(a.name));
  }, [agents, activeThread.data?.participants]);
  useThreadTailSSE(threadId);

  // Non-scroll-gated auto-page loop: fetch all remaining pages so the full
  // transcript renders on open — mirrors the audit-log keyset paging pattern
  // in _real-audit.ts but driven by a vanilla effect instead of a scroll
  // observer. Gated on hasNextPage && !isFetchingNextPage to prevent
  // double-fire / refetch-on-every-render storms (THR-098 fix #1).
  useEffect(() => {
    if (!threadId) return;
    if (activeMessagesQuery.hasNextPage && !activeMessagesQuery.isFetchingNextPage) {
      activeMessagesQuery.fetchNextPage().catch(() => {});
    }
  }, [threadId, activeMessagesQuery.hasNextPage, activeMessagesQuery.isFetchingNextPage, activeMessagesQuery.fetchNextPage]);

  const messages: ThreadMessage[] = useMemo(() => {
    if (activeMessagesQuery.data?.pages)
      return activeMessagesQuery.data.pages.flatMap((p) => p.messages);
    return activeThread.data?.messages ?? [];
  }, [activeMessagesQuery.data, activeThread.data]);

  // In-flight responders (working/queued) — the presence of any gates the
  // "Abort reply" control now rendered INSIDE the composer pill (THR-099 Phase A).
  const inFlightResponders = useMemo(() => selectInFlightResponders(messages), [messages]);

  const anyWorking = useMemo(
    () =>
      (activeThread.data?.reply_delivery ?? []).some((e) => e.state === 'running') ||
      messages.some((m) =>
        (m.responder_status ?? []).some((s) => s.status === 'working'),
      ),
    [messages, activeThread.data?.reply_delivery],
  );
  const nowMs = useNowMs(anyWorking);
  const knownAgentIds = useKnownAgentIds(agents);
  // Send mutation lives at the page level so the Composer pattern is pure.
  const sendFollowUp = useSendFollowUp(threadId ?? '');
  const preflight = usePreflightAddresses();
  const composerIdentityOptions = useIdentityOptions(composerAgents);
  const abortReplies = useAbortReplies(threadId ?? '');
  // THR-209 rename + pin mutations live at the page level; the detail toolbar
  // controls drive them.
  const renameMutation = useRenameThread(threadId ?? '');
  const pinMutation = useSetThreadPinned(threadId ?? '');
  // Product messages are held as catalog KEYS and rendered at render time so a
  // locale switch retranslates them in place (THR-118 W3a).
  const [pinError, setPinError] = useState<MessageKey | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState('');
  const [renameError, setRenameError] = useState<MessageKey | null>(null);
  const startRename = () => {
    setRenameDraft(activeThread.data?.subject ?? '');
    setRenameError(null);
    setRenaming(true);
  };
  const saveRename = async () => {
    if (!renameDraft.trim()) return;
    setRenameError(null);
    try {
      await renameMutation.mutateAsync({ subject: renameDraft });
      setRenaming(false);
    } catch {
      // On failure retain the typed value and show the inline error (retry).
      setRenameError('threads.page.renameFailed');
    }
  };
  const cancelRename = () => {
    setRenaming(false);
    setRenameError(null);
  };
  const togglePin = async (pinned: boolean) => {
    setPinError(null);
    try {
      await pinMutation.mutateAsync({ pinned });
    } catch {
      setPinError('threads.page.pinFailed');
    }
  };
  // Locale-neutral error descriptor, rendered through renderThreadError on
  // every render: mapped copy retranslates; raw diagnostics stay byte-exact.
  const [composerError, setComposerError] = useState<ThreadErrorView | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  // True for the WHOLE owned run (upload + send + late finalizer), so the
  // composer disables attach/send/remove from the first click. It is owned
  // synchronous run state rather than the surviving mutation observer's
  // `isPending`, whose pending flag would otherwise leak a departed run into
  // the replacement view (same thread id across orgs keeps one observer).
  const [submissionPending, setSubmissionPending] = useState(false);
  // Per-selection upload results/names are retained across a failed attempt so
  // a retry re-uploads only the selections that do not already have a ref.
  const attachmentRefsRef = useRef<Map<string, ThreadAttachmentRef>>(new Map());
  const attachmentNamesRef = useRef<Map<string, string>>(new Map());
  // Every name this page has allocated. Never reused, so a replacement
  // selection cannot `ArtifactStore.put`-overwrite an earlier artifact.
  const allocatedNamesRef = useRef<Set<string>>(new Set());
  // Ownership generation: a submission may only mutate the view's state while
  // it is still the origin (no thread switch / unmount in between).
  const submissionGenRef = useRef(0);

  useEffect(() => {
    // Any destination change — thread AND/OR org, including an org switch that
    // keeps the same thread id — invalidates in-flight submissions and discards
    // per-selection upload state so no ref/name can leak into the replacement
    // view. Keying only on threadId left the same-thread-id org switch unguarded.
    submissionGenRef.current += 1;
    attachmentRefsRef.current.clear();
    attachmentNamesRef.current.clear();
    setPendingAttachments([]);
    setSubmissionPending(false);
    setComposerError(null);
  }, [threadId, slug]);

  // Full unmount must also invalidate any in-flight submission's state writes.
  useEffect(() => () => { submissionGenRef.current += 1; }, []);

  const onAttachmentsChange = useCallback((next: PendingAttachment[]) => {
    const keep = new Set(next.map((item) => item.id));
    for (const id of [...attachmentRefsRef.current.keys()]) {
      if (!keep.has(id)) attachmentRefsRef.current.delete(id);
    }
    for (const id of [...attachmentNamesRef.current.keys()]) {
      if (!keep.has(id)) attachmentNamesRef.current.delete(id);
    }
    setPendingAttachments(next);
  }, []);

  // Dialog state
  const [showNew, setShowNew] = useState(false);
  const [showInvite, setShowInvite] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  // Participant pending removal — drives the confirm dialog; null keeps it closed.
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const openNew = () => {
    setShowNew(true);
  };

  const onSendFollowUp = async (markdown: string, attachments: PendingAttachment[]) => {
    if (!threadId || !slug) return;
    // Capture the destination + payload at first submit. A later thread/org
    // switch must not retarget this submission, and its late results must not
    // mutate the departee's replacement view.
    const capturedSlug = slug;
    const capturedThreadId = threadId;
    const generation = (submissionGenRef.current += 1);
    const isCurrent = () => submissionGenRef.current === generation;
    // Run-owned snapshot of the retained ref/name maps. Every read below uses
    // this copy only. Composer selection ids restart at `sel-1` on remount, so
    // reading the shared view maps after an await could otherwise substitute a
    // replacement view's selection (reviewer reproduction: alpha sent [a, y]).
    // Writes back to the view maps stay generation-guarded so a departed run
    // cannot seed the replacement cache.
    const runRefs = new Map(attachmentRefsRef.current);
    const runNames = new Map(attachmentNamesRef.current);
    const reserved = new Set(runNames.values());
    setComposerError(null);
    setSubmissionPending(true);
    // Identifies the selection whose upload failed; cleared once uploads
    // succeed so a later send failure is never blamed on the last file.
    let failedUpload: PendingAttachment | null = null;
    try {
      await preflight.mutateAsync({ slug: capturedSlug, context: 'thread_recipient', thread_id: capturedThreadId, body: markdown, canonicalAgentIds: knownAgentIds, bodyAgentIds: composerAgents.map(a => a.name), allowFounder: true });
      const refs: ThreadAttachmentRef[] = [];
      for (const pending of attachments) {
        failedUpload = pending;
        let ref = runRefs.get(pending.id);
        if (!ref) {
          let artifactName = runNames.get(pending.id);
          if (!artifactName) {
            artifactName = allocateArtifactName(
              capturedThreadId,
              pending.file,
              reserved,
              allocatedNamesRef.current,
            );
          }
          runNames.set(pending.id, artifactName);
          // Only the owning view may publish into the shared cache maps; a
          // departed submission's allocation must not be adopted by the view
          // that replaced it.
          if (isCurrent()) attachmentNamesRef.current.set(pending.id, artifactName);
          allocatedNamesRef.current.add(artifactName);
          const uploaded = await artifactsApi.uploadArtifact(capturedSlug, {
            file: pending.file,
            name: artifactName,
            agent: 'founder',
            // Existing thread: attribute the shared upload explicitly. Default
            // retention; no reason / expires_at.
            threadId: capturedThreadId,
            purpose: 'thread attachment',
            retention: 'default',
          });
          ref = {
            artifact_name: uploaded.name,
            display_name: pending.file.name,
            content_type: attachmentContentType(pending.file),
          };
          runRefs.set(pending.id, ref);
          if (isCurrent()) attachmentRefsRef.current.set(pending.id, ref);
          reserved.add(uploaded.name);
        }
        refs.push(ref);
      }
      failedUpload = null;
      await sendFollowUp.mutateAsync({
        body_markdown: markdown,
        ...(refs.length ? { attachments: refs } : {}),
        destination: { slug: capturedSlug, threadId: capturedThreadId },
      } as Parameters<typeof sendFollowUp.mutateAsync>[0]);
      if (isCurrent()) {
        attachmentRefsRef.current.clear();
        attachmentNamesRef.current.clear();
        setPendingAttachments([]);
      }
    } catch (err) {
      if (isCurrent()) {
        setComposerError({
          ...(failedUpload
            ? {
                label: {
                  key: 'threads.page.composer.uploadFailed' as MessageKey,
                  params: { name: failedUpload.file.name },
                },
              }
            : {}),
          detail:
            (failedUpload ? artifactRefusalDetail(err) : null) ??
            namingAddressError(err) ??
            classifyThreadError(err),
        });
      }
      throw err;
    } finally {
      if (isCurrent()) setSubmissionPending(false);
    }
  };

  // Inbox column — the full-width single-column conversation list shown on
  // /threads (no thread selected). When a thread IS selected the list column
  // collapses entirely (THREADDET-01 transcript-focus view): the detail column
  // takes the full width and the inbox is not rendered.
  // The list maps below name each thread `t`; alias the translator there.
  const tr = t;
  const rowLabels = (status: string) => ({
    statusLabel:
      threadStatusOrFallback(status) === 'open'
        ? tr('threads.page.row.statusOpen')
        : tr('threads.page.row.statusArchived'),
    fromDream: tr('threads.page.row.fromDream'),
    dreamBadge: tr('threads.page.dreamBadge'),
    last: tr('threads.page.row.last'),
  });
  const inbox = (
      <aside className="bg-surface-sunken h-full">
        {/* THR-099 a-threads list cap + THR-376 r2 A13: ONE <ContentWrap> is
            the page's single scroll owner (1180 `max-w-content` cap, 26px pad).
            The header scrolls with the rows, like TasksPage — no pinned header
            over a second inner list scroller. The scroll ref/onScroll (scroll
            restoration + infinite-load sentinel root) stay on this owner. */}
        <ContentWrap scrollRef={setListScrollRef} onScroll={observeListScroll}>
        <header className="border-border-default mb-4 border-b pb-4">
          {/* THREADS-04: uppercase eyebrow (org-wide thread + dream-opened
              counts) + page title (THR-376 PageTitle). */}
          <PageTitle
            eyebrow={totals ? t('threads.page.eyebrow', {
              count: totals.all,
              total: formatCountFor(locale, totals.all),
              dream: formatCountFor(locale, dreamOpenedCount ?? 0),
            }) : t('threads.page.list.unknown')}
            title={t('threads.page.title')}
          />
          {/* THR-376 r2 A1b: the page primary action lives in the AppBar. */}
          <TopbarActions>
            <Button
              size="sm"
              onClick={openNew}
              aria-label={t('threads.page.newThreadAria')}
              title={t('threads.page.newThreadTitle')}
            >
              {t('threads.page.newThread')}
            </Button>
          </TopbarActions>
          {/* THR-099: segmented All/Open/Archived pills on the LEFT, compact
              live-text Filter right-aligned on the SAME row (a-threads `.seg`
              row). The filter keeps its working substring match — just compact
              and right-pinned (EM ruling: do NOT downgrade to a ghost button).
              TASK-5987: the row wraps below `sm` so the filter drops to its
              own full-width line instead of pushing the tabs off-screen at
              375px; at sm+ the row is the same single line as before. */}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <SegmentedControl
              aria-label={t('threads.page.statusFilterAria')}
              value={bucket}
              onValueChange={(next) => {
                // Radix Tabs (the previous control) never re-fired for the
                // already-active bucket; keep a repeat click a no-op.
                if (next !== bucket) changeBucket(next);
              }}
              options={INBOX_BUCKETS.map((b) => ({
                value: b,
                label: (
                  <>
                    {t(BUCKET_LABEL_KEY[b])}
                    <span className="ml-1 text-xs tabular-nums opacity-60">
                      {counts[b] === undefined ? '…' : formatCountFor(locale, counts[b]!)}
                    </span>
                  </>
                ),
              }))}
            />
            <div className="flex-1" />
            <Input
              type="text"
              value={filter}
              onChange={(e) => changeFilter(e.target.value)}
              placeholder={t('threads.page.filterPlaceholder')}
              className="text-caption h-12 w-full shrink-0 px-2 py-1 sm:w-44"
              aria-label={t('threads.page.filterAria')}
            />
          </div>
          {(listQuery.isRefreshing || listQuery.isStale) && (
            <p role="status" className="text-text-muted mt-2 text-xs">
              {t(listQuery.isStale ? 'threads.page.list.stale' : 'threads.page.list.updating')}
            </p>
          )}
        </header>
          {/* THR-209: visible pin-failure banner (optimistic rollback). */}
          <PinErrorBanner message={pinError ? t(pinError) : null} />
          {/* Loading skeleton */}
          {bucketLoading && <InboxSkeleton />}

          {/* Error with retry — §2.5.5 */}
          {bucketError && (
            <div className="space-y-3 p-4 text-center">
              <p className="text-feedback-danger text-sm">{t('threads.page.list.errorTitle')}</p>
              <p className="text-text-muted text-xs">{t('threads.page.list.errorBody')}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => { void listQuery.retry(); }}
              >
                {t('threads.page.retry')}
              </Button>
            </div>
          )}

          {/* Empty — calm §2.5.5 */}
          {!bucketLoading && !bucketError && threads.length === 0 && !(filter.trim() && listQuery.hasNextPage) && (
            <EmptyState
              title={t('threads.page.list.emptyTitle')}
              body={
                <span>
                  {filter ? t('threads.page.list.filterEmpty') : t('threads.page.list.emptyBody')}
                </span>
              }
            />
          )}

          {/* Populated list — THR-209 msg 9: the Pinned section header + split
              render ONLY in the Open bucket (pinned ranked by numeric thread
              id desc from the server; unpinned in ordinary order). Archived
              and All buckets render one flat ordinary list — pin has zero
              presentation effect there — and the active query/filter still
              governs inclusion. Pin controls are detail-only. */}
          {!bucketLoading && threads.length > 0 && (
            <div className="overflow-hidden rounded-sm border border-border-default divide-y divide-border-default">
              {pinnedThreads.length > 0 && (
                <h2 className="text-text-muted px-1 pt-2 pb-1 text-xs font-semibold tracking-wider uppercase">
                  {t('threads.page.list.pinnedSection')}
                </h2>
              )}
              {pinnedThreads.map((t) => {
                const path = routes.detail(t.thread_id);
                const speaker = lastSpeakerChip(t.last_speaker, tr);
                return (
                  <InboxRow
                    key={t.thread_id}
                    threadId={t.thread_id}
                    subject={t.subject}
                    lastSpeaker={speaker ?? undefined}
                    status={threadStatusOrFallback(t.status)}
                    needsYou={false}
                    active={t.thread_id === threadId}
                    layout="thread"
                    fromDream={!!t.composed_from_dream_id}
                    meta={
                      <span className="whitespace-nowrap tabular-nums">
                        {relativeStartLabel(t.started_at, nowMs, tr)}
                      </span>
                    }
                    href={path}
                    onSelect={() => { rememberRowNavigationScroll(t.thread_id); navigate(path); }}
                    participants={t.participants}
                    participantLabels={(t.participants ?? []).map((id, index) => <span key={id}>{index > 0 && " · "}<IdentityName canonicalId={id} founderNameOnly /></span>)}
                    labels={rowLabels(t.status)}
                  />
                );
              })}
              {pinnedThreads.length > 0 && unpinnedThreads.length > 0 && (
                <h2 className="text-text-muted px-1 pt-2 pb-1 text-xs font-semibold tracking-wider uppercase">
                  {t('threads.page.list.threadsSection')}
                </h2>
              )}
              {unpinnedThreads.map((t) => {
                const path = routes.detail(t.thread_id);
                const speaker = lastSpeakerChip(t.last_speaker, tr);
                return (
                  <InboxRow
                    key={t.thread_id}
                    threadId={t.thread_id}
                    subject={t.subject}
                    lastSpeaker={speaker ?? undefined}
                    status={threadStatusOrFallback(t.status)}
                    needsYou={false}
                    active={t.thread_id === threadId}
                    layout="thread"
                    fromDream={!!t.composed_from_dream_id}
                    meta={
                      <span className="whitespace-nowrap tabular-nums">
                        {relativeStartLabel(t.started_at, nowMs, tr)}
                      </span>
                    }
                    href={path}
                    onSelect={() => { rememberRowNavigationScroll(t.thread_id); navigate(path); }}
                    participants={t.participants}
                    participantLabels={(t.participants ?? []).map((id, index) => <span key={id}>{index > 0 && " · "}<IdentityName canonicalId={id} founderNameOnly /></span>)}
                    labels={rowLabels(t.status)}
                  />
                );
              })}
            </div>
          )}
          {!bucketLoading && listQuery.hasNextPage && (
            <div ref={listSentinelRef} className="p-4 text-center">
              {filter.trim() && !bucketError && <p role="status" className="text-text-muted text-xs">{t('threads.page.list.searching')}</p>}
              <Button size="sm" variant="outline" disabled={bucketError || listQuery.isFetchingNextPage || listQuery.isRefreshing}
                onClick={() => { void listQuery.fetchNextPage(); }}>
                {t(listQuery.isFetchingNextPage ? 'threads.page.list.loadingMore' : 'threads.page.list.loadMore')}
              </Button>
            </div>
          )}
        </ContentWrap>
      </aside>
  );

  return (
    <>
      {threadId ? (
        <DetailColumn
          loading={activeThread.isLoading}
          errored={activeThread.isError || !activeThread.data}
          thread={activeThread.data}
          messages={messages}
          messagesLoading={activeMessagesQuery.isLoading}
          nowMs={nowMs}
          backHref={routes.inbox()}
          onInvite={() => setShowInvite(true)}
          onArchive={() => setShowArchive(true)}
          onRemoveParticipant={setRemoveTarget}
          // THR-209 rename + pin
          onRenameStart={startRename}
          renaming={renaming}
          renameDraft={renameDraft}
          onRenameDraftChange={setRenameDraft}
          onRenameSave={() => void saveRename()}
          onRenameCancel={cancelRename}
          renameError={renameError ? t(renameError) : null}
          renameSaving={renameMutation.isPending}
          onTogglePin={(pinned) => void togglePin(pinned)}
          pinPending={pinMutation.isPending}
          pinError={pinError ? t(pinError) : null}
          composer={
            <Composer
              agents={composerIdentityOptions}
              threadId={threadId ?? ''}
              orgSlug={slug ?? ''}
              disabled={activeThread.data?.status !== 'open'}
              pending={submissionPending}
              errorMessage={composerError !== null ? renderThreadError(composerError, t) : null}
              helper={t('threads.page.composer.placeholder')}
              hint={t('threads.page.composer.hint')}
              labels={{
                closedPlaceholder: t('threads.page.composer.closed'),
                attachFiles: t('threads.page.composer.attach'),
                textareaAria: t('threads.page.composer.textareaAria'),
                send: t('threads.page.composer.send'),
                sendTitle: t('threads.page.composer.sendTitle'),
                stop: t('threads.page.composer.stop'),
                abortReply: t('threads.page.composer.abort'),
                aborting: t('threads.page.composer.aborting'),
                removeAttachment: t('threads.page.composer.removeAttachment'),
                mentionList: t('threads.newThread.mentionList'),
              }}
              locale={locale}
              onSend={onSendFollowUp}
              attachments={pendingAttachments}
              onAttachmentsChange={onAttachmentsChange}
              // "Stop" (abort replies) lives INSIDE the input box (THR-099 Phase A).
              // Renders only while replies are in flight; one click aborts EVERY
              // in-flight reply (thread-level mutation, unchanged).
              abortReplies={{
                active: inFlightResponders.length > 0,
                isPending: abortReplies.isPending,
                onAbort: () => { abortReplies.mutateAsync().catch(() => {}); },
              }}
            />
          }
          slug={slug}
          threadId={threadId}
        />
      ) : (
        inbox
      )}

      <NewThreadDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        onCreated={(newId) => navigate(routes.detail(newId))}
        agents={agents}
      />
      {threadId && (
        <>
          <InviteDialog
            threadId={threadId}
            open={showInvite}
            onClose={() => setShowInvite(false)}
            agents={agents}
          />
          <ArchiveDialog
            threadId={threadId}
            open={showArchive}
            onClose={() => setShowArchive(false)}
          />
          <RemoveParticipantDialog
            threadId={threadId}
            agentName={removeTarget}
            open={removeTarget !== null}
            onClose={() => setRemoveTarget(null)}
          />

        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail column                                                      */
/* ------------------------------------------------------------------ */

interface DetailColumnProps {
  loading: boolean;
  errored: boolean;
  threadId: string | undefined;
  thread:
    | {
        thread_id: string;
        subject: string;
        status: string;
        started_at: string;
        participants: string[];
        summary: string | null;
        composed_from_dream_id?: string | null;
        reply_delivery?: ReplyDeliveryEntry[];
        pinned?: boolean;
        /** Desktop-app guest seats (THR-362); optional for older daemons. */
        guests?: ThreadGuest[];
      }
    | undefined;
  messages: ThreadMessage[];
  messagesLoading: boolean;
  nowMs: number;
  /** Back-link target — returns to the single-column thread list (THREADDET-01). */
  backHref: string;
  onInvite: () => void;
  onArchive: () => void;
  /** Open the confirm-remove dialog for the given participant. */
  onRemoveParticipant: (name: string) => void;
  /* ---- THR-209 rename + pin ---- */
  onRenameStart: () => void;
  renaming: boolean;
  renameDraft: string;
  onRenameDraftChange: (value: string) => void;
  onRenameSave: () => void;
  onRenameCancel: () => void;
  renameError: string | null;
  renameSaving: boolean;
  onTogglePin: (pinned: boolean) => void;
  pinPending: boolean;
  pinError: string | null;
  composer: JSX.Element;
  slug: string | undefined;
}

function DetailColumn({
  loading,
  errored,
  threadId,
  thread,
  messages,
  messagesLoading,
  nowMs,
  backHref,
  onInvite,
  onArchive,
  onRemoveParticipant,
  onRenameStart,
  renaming,
  renameDraft,
  onRenameDraftChange,
  onRenameSave,
  onRenameCancel,
  renameError,
  renameSaving,
  onTogglePin,
  pinPending,
  pinError,
  composer,
  slug,
}: DetailColumnProps): JSX.Element {
  const { t, locale } = useTranslation();
  const queryClient = useQueryClient();
  // Real produced artifacts aggregated from the transcript (THREADDET-02).
  // Computed before the early returns so the hook order stays stable.
  const artifacts = useMemo(() => collectThreadArtifacts(messages), [messages]);
  // Latest responder state per agent — feeds the participants rail status
  // column (honest, derived from the same responder_status the strip shows).
  const statusByAgent = useMemo(() => latestResponderStatusByAgent(messages), [messages]);
  // Direct dispatches and root retries; stable hook order, self-gates on threadId.
  const threadTasks = useThreadTasks(threadId);
  // Fresh (uncached input) token total for this thread — the "This thread ·
  // Fresh tokens" rail figure. Real, wired over the existing tokens rollup;
  // null (→ row omitted) when the thread has no recorded usage. Called before
  // the early returns to keep the hook order stable.
  const freshTokens = useThreadFreshTokens(threadId);
  // Desktop-app guests (THR-362 slice 4). Invite / New code / Revoke are
  // loopback founder routes that remote access denies (spec §7.3, decision
  // 13), so remote access hides those controls; the seat list stays.
  const remoteHost = useRemoteHost();
  const revokeGuest = useRevokeGuest(threadId ?? '');
  const replaceGuestCode = useReplaceGuestCode(threadId ?? '');
  // Guest dialog, pending seat and error belong to the org/thread they were
  // started on. DetailColumn is reused across thread routes, so each value
  // carries its scope: a value from another thread is never shown, is cleared
  // on navigation, and a late New code result for a departed thread is
  // dropped (it would invite the app into the wrong thread). Errors are kept
  // as catalog keys and translated at render, so a locale switch re-translates.
  const guestScope = `${slug ?? ''}/${threadId ?? ''}`;
  const guestScopeRef = useRef(guestScope);
  const [guestDialogState, setGuestDialog] = useState<{ scope: string; result: ThreadGuestInviteResponse | null } | null>(null);
  const [guestPendingState, setGuestPending] = useState<{ scope: string; id: string } | null>(null);
  const [guestErrorState, setGuestError] = useState<{ scope: string; error: GuestErrorKey } | null>(null);
  useEffect(() => {
    guestScopeRef.current = guestScope;
    setGuestDialog(null);
    setGuestError(null);
  }, [guestScope]);
  const guestDialog = guestDialogState?.scope === guestScope ? guestDialogState : null;
  const guestPendingId = guestPendingState?.scope === guestScope ? guestPendingState.id : null;
  const guestError = guestErrorState?.scope === guestScope ? guestErrorState.error : null;
  const maxSeq = useMemo(() => messages.reduce((max, m) => Math.max(max, m.seq), 0), [messages]);
  // Back affordance — the list column is collapsed in this view, so the link
  // back to the single-column thread list must stay reachable in every state.
  // THR-376: mockup .crumb — chevron + "All threads", rendered inside the
  // header block when the thread is loaded and on its own row otherwise.
  // Size comes from the wrapper: ThreadHeader's crumb slot sets the spec §2
  // 12.5px; the loading/error back row keeps the caption size.
  const crumb = (
    <Link
      to={backHref}
      className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 font-medium transition-colors"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M15 18l-6-6 6-6" />
      </svg>
      {t('threads.page.detail.back')}
    </Link>
  );
  const backNav = <div className="text-caption px-3.5 pt-4 sm:px-7">{crumb}</div>;

  // Loading skeleton
  if (loading) {
    return (
      <section className="flex h-full flex-col">
        {backNav}
        <div className="border-border-subtle animate-pulse space-y-2 border-b px-4 py-3">
          <div className="bg-surface-raised h-6 w-64 rounded-sm" />
          <div className="bg-surface-raised h-3 w-48 rounded-sm" />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <p className="text-text-muted text-body">{t('threads.page.detail.loadingMessages')}</p>
        </div>
      </section>
    );
  }

  // Error with retry — §2.5.5
  if (errored || !thread) {
    return (
      <section className="flex h-full flex-col">
        {backNav}
        <div className="flex flex-1 flex-col items-center justify-center space-y-3 p-4">
          <p className="text-feedback-danger text-body">{t('threads.page.detail.error')}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              queryClient.invalidateQueries({
                queryKey: ['thread', slug, threadId],
              });
              queryClient.invalidateQueries({
                queryKey: ['thread-messages', slug, threadId],
              });
            }}
          >
            {t('threads.page.retry')}
          </Button>
        </div>
      </section>
    );
  }

  const open = thread.status === 'open';
  const isDreamOriginated = !!thread.composed_from_dream_id;
  // Store-projected pair reply-delivery state (GH-688 Phase 1). Undefined on
  // older payloads / loading → empty list renders nothing (no live
  // obligation). Never inferred from per-message rows.
  const replyDelivery = thread.reply_delivery ?? [];
  const guests = thread.guests ?? [];
  const manageGuests = remoteHost === null;
  const runGuestAction = async (guest: ThreadGuest, op: (scope: string) => Promise<void>) => {
    if (guestPendingId !== null) return;
    const scope = guestScope;
    setGuestPending({ scope, id: guest.id });
    setGuestError(null);
    try {
      await op(scope);
    } catch (err) {
      if (guestScopeRef.current === scope) setGuestError({ scope, error: guestErrorKey(err) });
    } finally {
      setGuestPending((cur) => (cur?.scope === scope && cur.id === guest.id ? null : cur));
    }
  };
  const onGuestNewCode = (guest: ThreadGuest) => {
    void runGuestAction(guest, async (scope) => {
      const result = await replaceGuestCode.mutateAsync({ guestId: guest.id });
      if (guestScopeRef.current === scope) setGuestDialog({ scope, result });
    });
  };
  const onGuestRevoke = (guest: ThreadGuest) => {
    void runGuestAction(guest, async () => {
      await revokeGuest.mutateAsync({ guestId: guest.id });
    });
  };

  return (
    <section className="flex h-full flex-col">
      {/* THR-099 a-thread-detail cap: the mockup .td-grid caps the WHOLE
          two-pane surface (back link + header + convo + rail) at 1180 centered
          with height:100% and INTERNALLY-scrolling panes. Surgical mx-auto
          max-w-content wrapper — NOT ContentWrap, whose scroll-and-pad box
          would fight the full-height grid (plan §1b INNER-1180). The convo
          stream and rail keep their own overflow-auto; nothing functional
          changes — presentational cap only. */}
      <div className="max-w-content mx-auto flex min-h-0 w-full flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto md:flex-row md:overflow-hidden">
        <div className="flex min-h-64 min-w-0 shrink-0 flex-col md:min-h-0 md:flex-1">
          {/* THR-376 mockup .td-grid: the header (.td-head) sits at the top of
              the conversation column, so the rail runs the full height. */}
          <ThreadHeader
            threadId={thread.thread_id}
            subject={thread.subject}
            status={threadStatusOrFallback(thread.status)}
            participants={thread.participants}
            crumb={crumb}
            // Count of the messages loaded for this thread (THR-376 .td-sub).
            // null while the transcript is still loading: the sub-line shows
            // the thread id alone, never the raw participant-id fallback.
            messageCount={messagesLoading ? null : t('threads.page.header.messageCount', { count: messages.length })}
            archiveSummary={thread.summary}
            dreamOriginated={isDreamOriginated}
            renaming={renaming}
            renameDraft={renameDraft}
            onRenameDraftChange={onRenameDraftChange}
            onRenameSave={onRenameSave}
            onRenameCancel={onRenameCancel}
            renameError={renameError}
            renameSaving={renameSaving}
            labels={{
              statusOpen: t('threads.page.header.statusOpen'),
              statusArchived: t('threads.page.header.statusArchived'),
              noParticipants: t('threads.page.header.noParticipants'),
              archiveSummary: t('threads.page.header.archiveSummary'),
              titleInput: t('threads.page.header.titleInput'),
              save: t('threads.page.header.save'),
              cancel: t('threads.page.header.cancel'),
              dreamBadge: t('threads.page.dreamBadge'),
              pinned: t('threads.page.header.pinned'),
              pinThread: t('threads.page.action.pinThread'),
              unpinThread: t('threads.page.action.unpinThread'),
              moreActions: t('threads.page.action.more'),
              rename: t('threads.page.action.rename'),
              pin: t('threads.page.action.pin'),
              unpin: t('threads.page.action.unpin'),
              copyId: t('threads.page.action.copyId'),
              archive: t('threads.page.action.archive'),
            }}
            // THR-376 spec §2: Pin icon toggle + ⋯ menu (Rename, Pin/Unpin when
            // narrow, Copy thread ID, Archive) are built by the header from
            // these existing handlers. Invite lives in the Participants rail.
            pinned={!!thread.pinned}
            onTogglePin={onTogglePin}
            pinPending={pinPending}
            onRenameStart={onRenameStart}
            onArchive={onArchive}
            archiveDisabled={!open}
            // Resume (archived threads) is outside spec §2 but kept as-is.
            actions={thread.status === 'archived' ? <ResumeButton threadId={thread.thread_id} /> : undefined}
          />
          <PinErrorBanner message={pinError} />
          <div className="max-h-96 min-h-32 flex-1 overflow-auto md:max-h-none md:min-h-0">
            <ThreadDetailTranscript
              messages={messages}
              loading={messagesLoading}
              slug={slug}
              threadId={threadId}
              nowMs={nowMs}
              replyDelivery={replyDelivery}
              guests={guests}
            />
          </div>
          {/* THR-376 .composer: border-top wrapper, padding 12/28/16 (14px sides
              when narrow), content centred in the same 768px column as the
              transcript (PR2 container-query padding, max-w-3xl). */}
          <footer className="border-border-default @container shrink-0 border-t">
            <div className="px-3.5 pt-3 pb-4 @xl:px-7">
              <div className="mx-auto w-full max-w-3xl min-w-0">{composer}</div>
            </div>
          </footer>
        </div>
        {/* Stack properties below the conversation on narrow screens; md restores
            the 300px rail (THR-376 mockup .td-grid `1fr 300px`, .rail padding 20,
            sections 22px apart). Both panes stay mounted with every control. */}
        <aside
          aria-label={t('threads.page.rail.aria')}
          className="border-border-default md:w-rail-wide flex w-full min-w-0 shrink-0 flex-col gap-5.5 overflow-auto border-t p-5 md:border-t-0 md:border-l"
        >
          {/* Participants — mockup .prow: 7px LED + mono 12px name + 11.5px
              latest-response status. Status is derived from responder_status
              (honest); the founder row carries a "founder" label because the
              client knows the canonical `founder` id. No idle fallback. The
              remove control is a 22px icon button revealed on row hover or
              keyboard focus at md+ (always visible when the rail stacks); it
              stays in the tab order and keeps its accessible name. */}
          <div>
            <h3 className={RAIL_LABEL}>{t('threads.page.rail.participants')}</h3>
            {thread.participants.length > 0 ? (
              <ul>
                {thread.participants.map((p) => {
                  // Latest responder state drives BOTH the LED color and the
                  // status label (honest, derived — no fabrication). The
                  // founder shows their current name (canonical ID when
                  // unnamed/unavailable).
                  const respStatus = statusByAgent.get(p) ?? null;
                  const led = participantLed(p, respStatus);
                  const statusLabel =
                    p === 'founder'
                      ? t('threads.page.rail.founderRole')
                      : respStatus
                        ? responderStatusLabel(respStatus, t)
                        : null;
                  const displayName = <IdentityName canonicalId={p} founderNameOnly />;
                  return (
                    <li key={p} className="group text-body-sm flex items-center gap-2.25 py-1.5">
                      <span aria-hidden="true" className={`size-1.75 shrink-0 rounded-full ${led}`} />
                      <span className="text-text-primary text-caption min-w-0 truncate font-mono">{displayName}</span>
                      {statusLabel && (
                        <span className="text-text-secondary text-task-meta ml-auto shrink-0">{statusLabel}</span>
                      )}
                      {open && p !== 'founder' && (
                        <button
                          type="button"
                          aria-label={t('threads.page.rail.removeParticipant', { name: p })}
                          title={t('threads.page.rail.removeParticipant', { name: p })}
                          onClick={() => onRemoveParticipant(p)}
                          className={`${statusLabel ? 'ml-1.5' : 'ml-auto'} text-text-secondary hover:bg-danger-soft hover:text-feedback-danger focus-visible:ring-ring inline-flex size-5.5 shrink-0 items-center justify-center rounded-sm transition-opacity focus-visible:opacity-100 focus-visible:ring-2 focus-visible:outline-none md:opacity-0 md:group-hover:opacity-100`}
                        >
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className="size-3.25">
                            <path d="M18 6L6 18M6 6l12 12" />
                          </svg>
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-text-muted text-body-sm">{t('threads.page.rail.none')}</p>
            )}
            {/* Invite participant — mockup .rail-btn (dashed, full-width,
                left-aligned). Open-thread only; opens the InviteDialog. */}
            {open && (
              <button
                type="button"
                onClick={onInvite}
                title={t('threads.page.rail.inviteTitle')}
                className="border-border-strong text-caption text-text-secondary hover:border-accent-default hover:bg-accent-soft hover:text-accent-text focus-visible:ring-ring mt-1.5 flex w-full items-center justify-start gap-1.75 rounded-sm border border-dashed px-2.5 py-1.75 font-semibold transition-colors focus-visible:ring-2 focus-visible:outline-none"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className="size-3.5 shrink-0">
                  <circle cx="9" cy="8" r="3" />
                  <path d="M3.5 20a5.5 5.5 0 0110 0M18 8v6M15 11h6" />
                </svg>
                {t('threads.page.rail.invite')}
              </button>
            )}
            {/* Invite desktop app (THR-362 §4.2) — beside Invite participant,
                same dashed rail button; hidden through remote access. */}
            {open && manageGuests && (
              <button
                type="button"
                onClick={() => setGuestDialog({ scope: guestScope, result: null })}
                title={t('threads.page.rail.inviteGuestTitle')}
                className="border-border-strong text-caption text-text-secondary hover:border-accent-default hover:bg-accent-soft hover:text-accent-text focus-visible:ring-ring mt-1.5 flex w-full items-center justify-start gap-1.75 rounded-sm border border-dashed px-2.5 py-1.75 font-semibold transition-colors focus-visible:ring-2 focus-visible:outline-none"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" className="size-3.5 shrink-0">
                  <rect x="3" y="4" width="18" height="12" rx="2" />
                  <path d="M8 20h8M12 16v4" />
                </svg>
                {t('threads.page.rail.inviteGuest')}
              </button>
            )}
          </div>

          {/* Guests (THR-362 §13.2) — desktop-app seats in their own group
              under the agents. Seats are never participants. */}
          {guests.length > 0 && (
            <div>
              <h3 className={RAIL_LABEL}>{t('threads.page.rail.guests')}</h3>
              <GuestSeats
                threadId={thread.thread_id}
                guests={guests}
                maxSeq={maxSeq}
                formatTime={(iso) => guestTime(locale, iso)}
                relativeLabel={(iso) => relativeStartLabel(iso, Date.now(), t)}
                manageable={manageGuests}
                pendingGuestId={guestPendingId}
                onNewCode={onGuestNewCode}
                onRevoke={onGuestRevoke}
              />
              {guestError && (
                <p className="text-caption text-feedback-danger mt-1.5" role="alert">{guestErrorText(guestError, t)}</p>
              )}
              {!manageGuests && open && (
                <p className="text-caption text-text-secondary mt-1.5">{t('threads.page.guest.remoteNote')}</p>
              )}
            </div>
          )}

          {/* Reply delivery — STORE-PROJECTED pair state (GH-688 Phase 1
              Slice C). Renders only while any pair has a live obligation
              (running/queued/held/retry_required); a fully-settled thread omits
              the section entirely — no fabricated in-flight rows. The
              transcript tail mirrors this same list, and the per-message
              responder strips keep terminal history. */}
          {replyDelivery.length > 0 && (
            <div aria-label={t('threads.page.rail.replyDelivery')}>
              <h3 className={RAIL_LABEL}>{t('threads.page.rail.replyDelivery')}</h3>
              <ReplyDeliveryStrip entries={replyDelivery} nowMs={nowMs} />
            </div>
          )}

          {/* Linked tasks — mockup .rail-link: bordered rows carrying the
              shared StatusBadge + task EntityId (IdBadge), linking to the task
              as before. No task title (not on the payload). ACTIVE tasks
              (in_progress/pending/escalated/blocked) sort to the top; server
              order is preserved within each group (stable sort). ALL real
              linked tasks are shown — nothing is dropped. The list is capped to
              a bounded height with an INTERNAL scroll so a busy thread's task
              list cannot push "This thread" below the fold (layout bound, not
              a data cull); the header count signals when there are more. */}
          <div>
            <h3 className={RAIL_LABEL}>
              {t('threads.page.rail.linkedTasks')}
              {threadTasks.data && threadTasks.data.length > 0 && (
                <span className="text-text-disabled ml-1 tabular-nums normal-case">({formatCountFor(locale, threadTasks.data.length)})</span>
              )}
            </h3>
            {threadTasks.isLoading ? (
              <p className="text-text-muted text-body-sm">{t('threads.page.rail.tasksLoading')}</p>
            ) : threadTasks.isError ? (
              <p className="text-feedback-danger text-body-sm">{t('threads.page.rail.tasksError')}</p>
            ) : threadTasks.data && threadTasks.data.length > 0 ? (
              <ul className="flex max-h-44 flex-col gap-1.5 overflow-y-auto">
                {[...threadTasks.data]
                  .sort(
                    (a, b) =>
                      (isActiveTaskStatus(a.status) ? 0 : 1) -
                      (isActiveTaskStatus(b.status) ? 0 : 1),
                  )
                  .map((t) => {
                    const row = (
                      <>
                        <LinkedTaskStatus status={t.status} />
                        <IdBadge id={t.id} kind="task" />
                      </>
                    );
                    return (
                      <li key={t.id}>
                        {slug ? (
                          <Link
                            to={`/orgs/${slug}/tasks/${t.id}`}
                            className={`${RAIL_LINK} hover:bg-surface-sunken focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none`}
                          >
                            {row}
                          </Link>
                        ) : (
                          <div className={RAIL_LINK}>{row}</div>
                        )}
                      </li>
                    );
                  })}
              </ul>
            ) : (
              <p className="text-text-muted text-body-sm">{t('threads.page.rail.tasksEmpty')}</p>
            )}
          </div>

          {/* This thread — mockup .rail-row (13px, tabular numerals). Fresh
              tokens · Opened. "Fresh tokens" is the REAL per-thread
              fresh/uncached input total, wired over the existing tokens rollup
              and OMITTED when the thread has no recorded usage (never a
              fabricated figure). Status is shown in the header pill, so it is
              not duplicated here. */}
          <div>
            <h3 className={RAIL_LABEL}>{t('threads.page.rail.thisThread')}</h3>
            <dl>
              {typeof freshTokens.data === 'number' && (
                <div className={RAIL_ROW}>
                  <dt className="text-text-secondary">{t('threads.page.rail.freshTokens')}</dt>
                  <dd className="text-text-primary tabular-nums">
                    <StatValue value={freshTokens.data} align="inline" locale={locale} />
                  </dd>
                </div>
              )}
              <div className={RAIL_ROW}>
                <dt className="text-text-secondary">{t('threads.page.rail.opened')}</dt>
                <dd className="text-text-primary tabular-nums">
                  {new Date(thread.started_at).toLocaleDateString(locale, { month: 'short', day: 'numeric' })}
                </dd>
              </div>
            </dl>
          </div>

          {/* Artifacts — real attachments produced across the transcript,
              deduped by artifact_name; rendered only when some exist. Neutral
              text entries (the transcript bubbles carry the download links). */}
          {artifacts.length > 0 && (
            <div>
              <h3 className={RAIL_LABEL}>{t('threads.page.rail.artifacts')}</h3>
              <ul>
                {artifacts.map((a) => (
                  <li
                    key={a.thread_attachment_id ?? a.artifact_name}
                    className="text-text-secondary text-body-sm block truncate py-0.5"
                    title={a.display_name}
                  >
                    {a.display_name}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Thread ID */}
          <div>
            <h3 className={RAIL_LABEL}>{t('threads.page.rail.id')}</h3>
            <p className="text-text-secondary text-caption font-mono">{thread.thread_id}</p>
          </div>

          {/* Dream marker */}
          {isDreamOriginated && (
            <div>
              <h3 className={RAIL_LABEL}>{t('threads.page.rail.origin')}</h3>
              <span className="bg-accent-soft text-accent-text inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold">
                <CrescentMoonBadge className="h-3 w-3" label={t('threads.page.dreamBadge')} />
                {t('threads.page.rail.dream')}
              </span>
            </div>
          )}

          {/* Archive summary */}
          {thread.summary && (
            <div>
              <h3 className={RAIL_LABEL}>{t('threads.page.rail.summary')}</h3>
              <p className="text-text-secondary text-body-sm leading-relaxed">{thread.summary}</p>
            </div>
          )}
        </aside>
      </div>
      </div>
      {threadId && manageGuests && (
        <GuestInviteDialog
          key={guestScope}
          threadId={threadId}
          open={guestDialog !== null}
          initialResult={guestDialog?.result ?? null}
          onClose={() => setGuestDialog(null)}
        />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Thread detail transcript — turn cards                              */
/* ------------------------------------------------------------------ */

interface TranscriptProps {
  messages: ThreadMessage[];
  loading: boolean;
  slug?: string;
  threadId?: string;
  nowMs?: number;
  /** Store-projected pair reply-delivery state (GH-688 Phase 1 Slice C). */
  replyDelivery: ReplyDeliveryEntry[];
  /** Desktop-app guest seats (THR-362): labels for `guest:` speakers/events. */
  guests?: ThreadGuest[];
}

function ThreadDetailTranscript({ messages, loading, slug, threadId, nowMs, replyDelivery, guests }: TranscriptProps): JSX.Element {
  const { t, locale } = useTranslation();
  const guestLabels = useMemo(() => guestLabelMap(guests), [guests]);
  const endRef = useRef<HTMLDivElement>(null);
  // Re-renders the day separators when the local date rolls over.
  const todayKey = useLocalDayKey();

  // Live pair-level obligations from the STORE projection (queued/running).
  // These replace inferred per-message invocation rows for conversational
  // REPLY wakes — the store owns the single-wake truth. retry_required pairs
  // stay off the tail (they are diagnostics, never an active subprocess) and
  // are surfaced in the right-rail Reply delivery strip instead.
  const pairLive = useMemo(
    () => replyDelivery.filter((e) => e.state === 'queued' || e.state === 'running'),
    [replyDelivery],
  );
  const pairLiveAgents = useMemo(() => new Set(pairLive.map((e) => e.agent_name)), [pairLive]);
  // Inferred in-flight rows NOT covered by a pair entry. Suppression is
  // purpose-aware (GH-688 Phase 1 Slice C reviewer finding; TASK-5553): only a
  // conversational REPLY wake — identified by the AUTHORITATIVE wire purpose
  // carried from thread_invocations, never by the triggering row's kind — is
  // masked when the store projection already owns that agent's pair.
  // Special-purpose wakes (TASK_FOLLOWUP / BOOTSTRAP) are intentionally
  // outside reply_delivery and ALWAYS preserved — even when the same agent
  // concurrently holds a REPLY pair, so the followup in-flight strip keeps
  // working (THR-061). A REPLY whose coalesced range anchors on a SYSTEM row
  // still reads purpose='reply' on the wire and is therefore suppressed next
  // to its pair row — exactly one replying bubble (founder THR-198 seq 77).
  const inferredInFlight = useMemo(
    () =>
      selectInFlightResponders(messages).filter(
        (s) => !(s.purpose === 'reply' && pairLiveAgents.has(s.agent_name)),
      ),
    [messages, pairLiveAgents],
  );
  const inFlightKey =
    pairLive.map((e) => `${e.agent_name}:${e.state}`).join(',') +
    '|' +
    inferredInFlight.map((s) => `${s.agent_name}:${s.purpose}:${s.status}`).join(',');

  const typingAria = (agent: string, working: boolean) =>
    working
      ? t('threads.page.typing.replyingAria', { agent })
      : t('threads.page.typing.queuedAria', { agent });

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages.length, inFlightKey]);

  // Error with retry for messages
  if (loading && messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted text-caption">{t('threads.page.detail.loadingMessages')}</p>
      </div>
    );
  }

  if (!loading && messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted text-caption">{t('threads.page.detail.noMessages')}</p>
      </div>
    );
  }

  const rows = buildTranscriptRows(messages);
  const formatTime = (iso: string) => messageTime(locale, iso);
  const formatTitle = (iso: string) => localeTimestamp(locale, iso);

  return (
    // THR-376 mockup .stream / .stream-inner: a centred column (nearest
    // token max-w-3xl = 768px; mockup 780px) with 18px between turns. The
    // padding follows the COLUMN width (container query), not the viewport:
    // with both rails open at 768-800px the column is only ~250px wide.
    // `relative` makes this scroller the containing block for the sr-only
    // (position:absolute) speaker/system-event labels; without it they escape
    // to the initial containing block and window-scroll the whole app (MEM-116).
    <div className="@container relative h-full overflow-auto">
      <div className="px-3.5 pt-1 pb-5 @xl:px-7 @xl:pt-2 @xl:pb-7">
      <div className="mx-auto flex w-full max-w-3xl min-w-0 flex-col">
        {rows.map((row, i) => {
          if (row.type === 'day') {
            return <DaySeparator key={`day-${row.key}`} iso={row.iso} todayKey={todayKey} />;
          }
          const m = row.message;
          const variant = messageVariant(m);
          const prev = rows[i - 1];
          const prevIsEvent = prev?.type === 'message' && isEventRow(prev.message);
          const key = `${m.seq}-${m.speaker}-${m.kind}`;
          if (variant === 'system' || variant === 'decline') {
            // System events and declines are quiet centred chips (mockup
            // .evt); consecutive events sit 6px apart, otherwise 16px.
            // Terminal/working responder receipts stay under the event.
            return (
              <div key={key} className={prevIsEvent ? 'mt-1.5' : 'mt-4'}>
                {variant === 'decline' ? (
                  <DeclineEvent message={m} formatTime={formatTime} formatTitle={formatTitle} />
                ) : (
                  <SystemEvent
                    timestamp={m.created_at}
                    systemPayload={m.system_payload}
                    guests={guests}
                    slug={slug}
                    formatTime={formatTime}
                    formatTitle={formatTitle}
                  />
                )}
                <ResponderStatusStrip statuses={m.responder_status ?? []} nowMs={nowMs} align="center" />
              </div>
            );
          }
          const bubble = (
            <MessageBubble
              variant={variant}
              seq={m.seq}
              speaker={m.speaker}
              speakerLabel={
                isGuestSpeaker(m.speaker) ? (
                  // Guest speaker (THR-362 §5.4): the seat label plus a guest
                  // badge; never an IdentityName lookup (a guest is no agent).
                  <span className="inline-flex min-w-0 items-baseline gap-1.5">
                    <span className="min-w-0 truncate">{guestSpeakerLabel(m.speaker, guestLabels)}</span>
                    <StatusBadge tone="attention" label={t('threads.page.guest.badge')} />
                  </span>
                ) : (
                  <IdentityName canonicalId={m.speaker} founderNameOnly />
                )
              }
              speakerRole={m.speaker === 'founder' ? 'founder' : 'worker'}
              timestamp={m.created_at}
              body={m.body_markdown}
              declineReason={m.decline_reason}
              attachments={m.attachments}
              labels={{
                declined: t('threads.page.message.declined'),
                mermaidLoading: t('common.mermaidLoading'),
                remoteImage: t('common.remoteImageBlocked'),
                systemEvent: t('threads.page.system.event'),
              }}
              formatTimestamp={formatTime}
              formatTimestampTitle={formatTitle}
              onAttachmentDownload={slug && threadId ? (attachment) => {
                if (attachment.thread_attachment_id) {
                  artifactsApi.downloadThreadAttachment(slug, threadId, attachment.thread_attachment_id, attachment.display_name);
                } else {
                  artifactsApi.downloadArtifact(slug, attachment.artifact_name);
                }
              } : undefined}
            />
          );
          const receipts = m.kind === 'message' && (
            <ResponderStatusStrip
              statuses={m.responder_status ?? []}
              nowMs={nowMs}
              align={variant === 'founder' ? 'end' : 'start'}
            />
          );
          if (variant === 'founder') {
            // Founder turn (mockup .mine): right-aligned, no avatar, receipt
            // pills right-aligned under the bubble.
            return (
              <div key={key} className="mt-4.5 min-w-0">
                {bubble}
                {receipts}
              </div>
            );
          }
          // Agent turn (mockup .msg): avatar + card column; the receipt
          // strip lives inside the column so it aligns under the card.
          return (
            <div key={key} className="mt-4.5 flex min-w-0 gap-3">
              <TurnAvatar name={m.speaker} guestLabel={guestLabels.get(m.speaker)} />
              <div className="min-w-0 flex-1">
                {bubble}
                {receipts}
              </div>
            </div>
          );
        })}
        {pairLive.map((e) => (
          // Same avatar-indented turn structure as agent turns above so the
          // in-flight bubble lines up with the agent cards.
          <div key={`typing-pair-${e.agent_name}`} className="mt-4.5 flex min-w-0 gap-3">
            <TurnAvatar name={e.agent_name} />
            <div className="min-w-0 flex-1">
              <TypingBubble
                agentName={e.agent_name}
                agentLabel={<IdentityName canonicalId={e.agent_name} />}
                status={e.state === 'running' ? 'working' : 'queued'}
                startedAt={e.started_at}
                nowMs={nowMs}
                // Honest store-projected caption: queued carries the coalesced
                // count + inclusive range (never an active-subprocess claim),
                // running carries the claimed immutable range.
                caption={replyDeliveryCaption(e, t, nowMs)}
                ariaLabel={typingAria(e.agent_name, e.state === 'running')}
                // "Abort reply" moved INTO the composer input pill (THR-099 Phase A,
                // founder seq57). The generic `trailing` slot is intentionally left
                // unused here — no abort control renders beside the replying row.
              />
            </div>
          </div>
        ))}
        {inferredInFlight.map((s) => (
          <div key={`typing-inf-${s.agent_name}-${s.purpose}`} className="mt-4.5 flex min-w-0 gap-3">
            <TurnAvatar name={s.agent_name} />
            <div className="min-w-0 flex-1">
              <TypingBubble
                agentName={s.agent_name}
                agentLabel={<IdentityName canonicalId={s.agent_name} />}
                status={s.status as 'queued' | 'working'}
                startedAt={s.started_at}
                nowMs={nowMs}
                caption={
                  s.status === 'working'
                    ? t('threads.page.typing.replying', {
                        elapsed: formatElapsed(s.started_at, nowMs ?? Date.now()),
                      }).trimEnd()
                    : t('threads.page.typing.queued')
                }
                ariaLabel={typingAria(s.agent_name, s.status === 'working')}
                // "Abort reply" moved INTO the composer input pill (THR-099 Phase A,
                // founder seq57). The generic `trailing` slot is intentionally left
                // unused here — no abort control renders beside the replying row.
              />
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Day separators (THR-376 mockup .day)                               */
/* ------------------------------------------------------------------ */

type TranscriptRow =
  | { type: 'day'; key: string; iso: string }
  | { type: 'message'; message: ThreadMessage };

/** Host-local calendar-day key for a timestamp ('' when unparseable). */
function localDayKey(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/**
 * Interleave a day separator before the first message of every host-local
 * calendar day, computed client-side from created_at. Messages with an
 * unparseable timestamp never open a new day.
 */
function buildTranscriptRows(messages: ThreadMessage[]): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  let lastDay = '';
  for (const m of messages) {
    const day = localDayKey(m.created_at);
    if (day && day !== lastDay) {
      rows.push({ type: 'day', key: day, iso: m.created_at });
      lastDay = day;
    }
    rows.push({ type: 'message', message: m });
  }
  return rows;
}

function DaySeparator({ iso }: { iso: string; todayKey: string }): JSX.Element {
  // todayKey (from useLocalDayKey) changes at local midnight; the new prop
  // re-renders this separator so the label is recomputed against the clock.
  const { t, locale } = useTranslation();
  const d = new Date(iso);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const date = d.toLocaleDateString(locale, {
    month: 'short',
    day: 'numeric',
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }),
  });
  const label =
    d.toDateString() === now.toDateString()
      ? t('threads.page.day.today', { date })
      : d.toDateString() === yesterday.toDateString()
        ? t('threads.page.day.yesterday', { date })
        : date;
  return (
    <div
      role="separator"
      aria-label={label}
      className="text-caption text-text-secondary mt-5.5 mb-1.5 flex items-center gap-3 font-semibold"
    >
      <span aria-hidden="true" className="bg-border-default h-px flex-1" />
      <span aria-hidden="true" className="shrink-0">{label}</span>
      <span aria-hidden="true" className="bg-border-default h-px flex-1" />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  System events — centred chips (THR-376 mockup .evt / .chip-evt)    */
/* ------------------------------------------------------------------ */

function isEventRow(m: ThreadMessage): boolean {
  return m.kind === 'system' || m.kind === 'decline';
}

const EVENT_CHIP =
  'text-caption text-text-secondary bg-surface-sunken border-border-default inline-flex max-w-full min-w-0 items-center gap-1.75 rounded-lg border py-0.75 pr-2.75 pl-2.25';
const EVENT_TIME = 'text-mono-sm text-text-secondary shrink-0 font-mono';
const EVENT_AGENT = 'text-mono-sm text-text-primary font-mono font-semibold';

interface SystemEventProps {
  timestamp: string;
  systemPayload: Record<string, unknown> | null;
  guests?: ThreadGuest[];
  slug?: string;
  formatTime: (iso: string) => string;
  formatTitle: (iso: string) => string;
}

function SystemEvent({ timestamp, systemPayload, guests, slug, formatTime, formatTitle }: SystemEventProps): JSX.Element {
  const { t, render, locale } = useTranslation();
  const tag = String(systemPayload?.kind_tag ?? systemPayload?.event ?? '');
  if (tag === 'task_escalated' && systemPayload) {
    return (
      <EscalationCard
        payload={systemPayload}
        slug={slug}
        timestamp={timestamp}
        formatTime={formatTime}
        formatTitle={formatTitle}
      />
    );
  }
  const description = describeGuestSystem(systemPayload, guests, locale, t, render)
    ?? describeSystem(systemPayload, slug, t, render);
  const Icon = SYSTEM_EVENT_ICON[tag] ?? Info;
  return (
    <div className="flex justify-center">
      <span className={EVENT_CHIP}>
        <Icon aria-hidden="true" className="text-text-muted size-3.25 shrink-0" />
        {/* pre-wrap keeps authored line breaks/paragraphs (e.g. a
            task_completed final_output_summary), as before the restyle. */}
        <span className="min-w-0 break-words whitespace-pre-wrap">
          <span className="sr-only">{t('threads.page.system.event')}: </span>
          {description}
        </span>
        <time dateTime={timestamp} title={formatTitle(timestamp)} className={EVENT_TIME}>
          {formatTime(timestamp)}
        </time>
      </span>
    </div>
  );
}

const SYSTEM_EVENT_ICON: Record<string, LucideIcon> = {
  invited: UserPlus,
  participant_added: UserPlus,
  participant_removed: UserMinus,
  archive_requested: Archive,
  archived: Archive,
  resumed: RotateCcw,
  task_dispatched: Send,
  task_completed: CircleCheck,
  task_failed: CircleX,
  thread_guest_invited: UserPlus,
  thread_guest_joined: UserPlus,
  thread_guest_ended: UserMinus,
};

/**
 * Desktop-app guest seat events (THR-362 §4.3/§4.6): the daemon writes
 * `system_payload = {kind_tag, guest_id: "G-n", label[, cause]}` from
 * `runtime/infrastructure/db/thread_guests.py`. Product phrasing is
 * translated; the label and an unknown cause render verbatim. The seat's
 * expiry comes from the thread's guest projection when present. Returns null
 * for every other tag.
 */
function describeGuestSystem(
  payload: Record<string, unknown> | null,
  guests: ThreadGuest[] | undefined,
  locale: Locale,
  t: Translate,
  render: RenderTranslated,
): React.ReactNode | null {
  const tag = String(payload?.kind_tag ?? '');
  if (!payload || !tag.startsWith('thread_guest_')) return null;
  const seatId = `guest:${String(payload.guest_id ?? '')}`;
  const seat = guests?.find((g) => g.id === seatId);
  const label = payload.label ? String(payload.label) : (seat?.label ?? seatId);
  const guest = <span className={EVENT_AGENT}>{label}</span>;
  const time = seat ? guestTime(locale, seat.expires_at) : null;
  switch (tag) {
    case 'thread_guest_invited':
      return time
        ? render('threads.page.system.guestInvitedUntil', { guest, time })
        : render('threads.page.system.guestInvited', { guest });
    case 'thread_guest_joined':
      return time
        ? render('threads.page.system.guestJoinedUntil', { guest, time })
        : render('threads.page.system.guestJoined', { guest });
    case 'thread_guest_ended':
      return render('threads.page.system.guestEnded', { guest, cause: guestEndCauseLabel(payload.cause, t) });
    default:
      return null;
  }
}

/**
 * Decline (kind=decline) as an attention chip (mockup .chip-evt.attn) with a
 * "Why?" expander. The expander shows the decline_reason the client already
 * has, verbatim; it is omitted when there is no reason.
 */
function DeclineEvent({
  message,
  formatTime,
  formatTitle,
}: {
  message: ThreadMessage;
  formatTime: (iso: string) => string;
  formatTitle: (iso: string) => string;
}): JSX.Element {
  const { t, render } = useTranslation();
  const [open, setOpen] = useState(false);
  const detailId = `decline-why-${message.seq}`;
  const reason = message.decline_reason?.trim() || null;
  return (
    <>
      <div className="flex justify-center">
        <span className={EVENT_CHIP}>
          <TriangleAlert aria-hidden="true" className="text-attention-text size-3.25 shrink-0" />
          <span className="min-w-0 break-words">
            {render('threads.page.decline.chip', {
              agent: <span className={EVENT_AGENT}><IdentityName canonicalId={message.speaker} founderNameOnly /></span>,
            })}
          </span>
          <time dateTime={message.created_at} title={formatTitle(message.created_at)} className={EVENT_TIME}>
            {formatTime(message.created_at)}
          </time>
          {reason && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={detailId}
              onClick={() => setOpen((v) => !v)}
              className="text-info shrink-0 cursor-pointer font-semibold hover:underline"
            >
              {open ? t('threads.page.decline.hide') : t('threads.page.decline.why')}
            </button>
          )}
        </span>
      </div>
      {reason && (
        <p
          id={detailId}
          hidden={!open}
          className="text-caption text-text-secondary mx-auto mt-1.5 max-w-lg text-center leading-relaxed break-words whitespace-pre-wrap"
        >
          {reason}
        </p>
      )}
    </>
  );
}

/**
 * task_escalated as a card (mockup .esc): 3px attention left border, the
 * payload reason clamped to two lines with Show more / Show less, and an
 * Open task link. Built only from the task_escalated payload (task_id,
 * reason); a missing field simply drops its part.
 */
function EscalationCard({
  payload,
  slug,
  timestamp,
  formatTime,
  formatTitle,
}: {
  payload: Record<string, unknown>;
  slug?: string;
  timestamp: string;
  formatTime: (iso: string) => string;
  formatTitle: (iso: string) => string;
}): JSX.Element {
  const { t, render } = useTranslation();
  const taskId = String(payload.task_id ?? '');
  const reason = payload.reason ? String(payload.reason) : null;
  const [open, setOpen] = useState(false);
  const [clamped, setClamped] = useState(false);
  const bodyRef = useRef<HTMLParagraphElement>(null);
  const bodyId = `esc-body-${taskId || 'unknown'}-${timestamp}`;

  // Show more only when the 2-line clamp actually hides text.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (!el || open) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [reason, open]);

  return (
    <div className="bg-surface-raised border-border-default border-l-attention mx-auto w-full max-w-xl rounded-md border border-l-3 px-3.5 py-2.75">
      <div className="text-caption flex min-w-0 items-center gap-2">
        <TriangleAlert aria-hidden="true" className="text-attention-text size-3.5 shrink-0" />
        <span className="min-w-0 font-semibold break-words">
          {render('threads.page.system.taskEscalated', {
            task: <span className="font-mono">{taskId}</span>,
          })}
        </span>
        <time dateTime={timestamp} title={formatTitle(timestamp)} className={`${EVENT_TIME} ml-auto`}>
          {formatTime(timestamp)}
        </time>
      </div>
      {reason && (
        <p
          ref={bodyRef}
          id={bodyId}
          className={`text-caption text-text-secondary mt-1.25 leading-relaxed break-words whitespace-pre-wrap ${open ? '' : 'line-clamp-2'}`}
        >
          {reason}
        </p>
      )}
      {((reason && (clamped || open)) || (slug && taskId)) && (
        <div className="text-caption mt-2 flex items-center gap-3">
          {reason && (clamped || open) && (
            <button
              type="button"
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={() => setOpen((v) => !v)}
              className="text-info cursor-pointer font-semibold hover:underline"
            >
              {open ? t('threads.page.escalation.showLess') : t('threads.page.escalation.showMore')}
            </button>
          )}
          {slug && taskId && (
            <Link to={`/orgs/${slug}/tasks/${taskId}`} className="text-info font-semibold hover:underline">
              {t('threads.page.escalation.openTask')}
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Message variant logic (excludes ran: cards — D7 deferred)          */
/* ------------------------------------------------------------------ */

function messageVariant(m: ThreadMessage): MessageVariant {
  // Omit in-thread agent-own ran: cards (D7 deferred, P1)
  // These would be system messages with kind_tag matching ran: command patterns
  if (m.kind === 'system') return 'system';
  if (m.kind === 'decline') return 'decline';
  if (m.speaker === 'founder') return 'founder';
  return 'worker';
}

/* ------------------------------------------------------------------ */
/*  System event descriptions                                          */
/* ------------------------------------------------------------------ */

function describeSystem(
  payload: Record<string, unknown> | null,
  slug: string | undefined,
  t: Translate,
  render: RenderTranslated,
): React.ReactNode {
  // Product event phrasing is translated; payload values (agent names, task
  // ids, summaries, reasons, unknown tags / raw JSON) render verbatim.
  if (!payload) return t('threads.page.system.event');
  const tag = String(payload.kind_tag ?? payload.event ?? '');
  const taskLinkFor = (taskId: string): React.ReactNode =>
    slug && taskId
      ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
      : taskId;
  // Agent names in event chips: mono 600 ink (mockup .chip-evt b), verbatim.
  const agentFor = (value: unknown): React.ReactNode => (
    <span className={EVENT_AGENT}>{String(value)}</span>
  );
  switch (tag) {
    case 'invited':
      return render('threads.page.system.invited', { agent: agentFor(payload.agent) });
    case 'participant_added':
      return render('threads.page.system.participantAdded', { agent: agentFor(payload.agent_name) });
    case 'participant_removed':
      return render('threads.page.system.participantRemoved', { agent: agentFor(payload.agent_name) });
    case 'archive_requested':
      return t('threads.page.system.archiveRequested');
    case 'archived':
      return t('threads.page.system.archived');
    case 'resumed':
      return t('threads.page.system.resumed');
    case 'task_dispatched': {
      const taskId = String(payload.task_id ?? '');
      return render('threads.page.system.taskDispatched', { task: taskLinkFor(taskId) });
    }
    case 'task_completed': {
      const taskId = String(payload.task_id ?? '');
      const summary = payload.final_output_summary
        ? String(payload.final_output_summary)
        : null;
      return (
        <>
          {render('threads.page.system.taskCompleted', { task: taskLinkFor(taskId) })}
          {summary ? ` · ${summary}` : ''}
        </>
      );
    }
    case 'task_failed': {
      const taskId = String(payload.task_id ?? '');
      const cancelledSuffix = payload.cancelled ? t('threads.page.system.founderCancelled') : '';
      const revisitTaskId = payload.revisit_task_id ? String(payload.revisit_task_id) : null;
      const chainLength = typeof payload.revisit_chain_length === 'number' ? payload.revisit_chain_length : 1;
      let revisitSuffix: React.ReactNode = null;
      if (revisitTaskId) {
        revisitSuffix = render('threads.page.system.revisiting', { task: taskLinkFor(revisitTaskId) });
      } else if (chainLength > 1) {
        revisitSuffix = t('threads.page.system.noFurtherRevisits');
      }
      return (
        <>
          {render('threads.page.system.taskFailed', { task: taskLinkFor(taskId) })}
          {cancelledSuffix}
          {revisitSuffix}
        </>
      );
    }
    case 'task_escalated': {
      const taskId = String(payload.task_id ?? '');
      const reason = payload.reason ? String(payload.reason) : null;
      return (
        <>
          {render('threads.page.system.taskEscalated', { task: taskLinkFor(taskId) })}
          {reason ? ` · ${reason}` : ''}
        </>
      );
    }
    default:
      return tag || JSON.stringify(payload);
  }
}
