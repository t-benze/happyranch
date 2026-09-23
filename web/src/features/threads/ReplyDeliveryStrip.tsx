import type { ReplyDeliveryEntry } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';

/** The `t` translator from `useTranslation()` — passed explicitly so the
 *  caption helper stays a pure function shared with the transcript tail. */
export type DeliveryTranslator = (key: MessageKey, params?: MessageParams) => string;

/**
 * Compact per-pair reply-delivery rows (GH-688 Phase 1 Slice C).
 *
 * Renders the STORE-PROJECTED pair state from the wire ``reply_delivery``
 * list — never inferred from per-message invocation rows, never fabricating
 * per-covered-message state. Four honest states:
 *
 *  - ``queued``         — one unstarted coalesced wake; NOT an active
 *                         subprocess (static dot, muted text).
 *  - ``running``        — one claimed in-flight reply with an immutable
 *                         inclusive range; ``started_at`` is the only
 *                         subprocess evidence.
 *  - ``held``           — healthy neutral waiting under an authoritative
 *                         open exchange + matching held participant row;
 *                         never rendered as typing or a fault.
 *  - ``retry_required`` — unacknowledged range with no active wake; only its
 *                         bounded current failure category may be captioned,
 *                         never raw terminal detail or typing.
 *
 * A fully-settled pair is omitted from the projection, so an empty list
 * renders nothing (callers hide the whole section).
 */
export function ReplyDeliveryStrip({
  entries,
  nowMs,
}: {
  entries: ReplyDeliveryEntry[];
  nowMs?: number;
}): JSX.Element | null {
  const { t } = useTranslation();
  if (entries.length === 0) return null;
  const now = nowMs ?? Date.now();
  const running = entries.filter((entry) => entry.state === 'running');
  const queued = entries.filter((entry) => entry.state === 'queued');
  const held = entries.filter((entry) => entry.state === 'held');
  const diagnostics = entries.filter((entry) => entry.state === 'retry_required');
  return (
    <div className="space-y-2">
      {entries.length > 1 && (
        <p className="text-text-disabled text-caption tabular-nums">
          {t('threads.delivery.currentCount', { count: entries.length })}
        </p>
      )}

      {running.length > 0 && (
        <ul aria-label={t('threads.delivery.activeList')} className="space-y-1.5">
          {running.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} t={t} />)}
        </ul>
      )}

      {queued.length > 0 && (
        <details
          aria-label={t('threads.delivery.queuedSummary', { count: queued.length })}
          className="border-border-default rounded-md border"
        >
          <summary className="text-text-secondary hover:bg-surface-raised marker:text-text-muted cursor-pointer rounded-md px-2 py-1.5 text-xs font-medium focus-visible:outline-2 focus-visible:outline-offset-2">
            {t('threads.delivery.queuedSummary', { count: queued.length })}
          </summary>
          <ul aria-label={t('threads.delivery.queuedList')} className="border-border-default space-y-1.5 border-t px-2 py-2">
            {queued.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} t={t} />)}
          </ul>
        </details>
      )}

      {held.length > 0 && (
        <ul aria-label={t('threads.delivery.heldList')} className="space-y-1.5">
          {held.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} t={t} />)}
        </ul>
      )}

      {diagnostics.length > 0 && (
        <ul aria-label={t('threads.delivery.diagnosticsList')} className="space-y-1.5">
          {diagnostics.map((entry) => <DeliveryRow key={entry.agent_name} entry={entry} nowMs={now} t={t} />)}
        </ul>
      )}
    </div>
  );
}

function DeliveryRow({
  entry,
  nowMs,
  t,
}: {
  entry: ReplyDeliveryEntry;
  nowMs: number;
  t: DeliveryTranslator;
}): JSX.Element {
  return (
    <li className="flex min-w-0 items-start gap-1.5">
      <span aria-hidden="true" className={`mt-1 h-1.5 w-1.5 rounded-full ${dotClass(entry.state)}`} />
      <span className="min-w-0">
        <span className="text-text-primary block font-mono text-xs leading-tight break-all">
          {entry.agent_name}
        </span>
        <span className={`text-caption block leading-snug break-words ${stateClass(entry.state)}`}>
          {replyDeliveryCaption(entry, t, nowMs)}
        </span>
      </span>
    </li>
  );
}

/** Localized compact elapsed label ("1m" / "1 分钟") — same buckets as
 *  `formatElapsed` (whole seconds under a minute, else whole minutes). Returns
 *  '' when there is no start instant, exactly like `formatElapsed`. */
export function localizedElapsed(
  startedAt: string | null,
  nowMs: number,
  t: DeliveryTranslator,
): string {
  if (!startedAt) return '';
  const secs = Math.max(0, Math.floor((nowMs - Date.parse(startedAt)) / 1000));
  if (secs < 60) return t('threads.delivery.elapsedSeconds', { count: secs });
  return t('threads.delivery.elapsedMinutes', { count: Math.floor(secs / 60) });
}

/** Bounded failure categories with product labels. An unrecognized category
 *  keeps the legacy humanized raw value (underscores → spaces) verbatim. */
const FAILURE_CATEGORY_KEYS: Record<string, MessageKey> = {
  no_callback: 'threads.delivery.category.noCallback',
  no_callback_after_reprompt: 'threads.delivery.category.noCallbackAfterReprompt',
  infra_fail: 'threads.delivery.category.infraFail',
};

/** One honest compact caption per pair state — shared by the rail strip and
 *  the transcript-tail live indicator so both surfaces agree verbatim. `t` is
 *  the caller's translator (`useTranslation().t`); sequence numbers stay data. */
export function replyDeliveryCaption(
  e: ReplyDeliveryEntry,
  t: DeliveryTranslator,
  nowMs?: number,
): string {
  const range =
    e.from_seq === e.through_seq
      ? t('threads.delivery.rangeSingle', { seq: e.from_seq })
      : t('threads.delivery.rangeMulti', { from: e.from_seq, through: e.through_seq });
  switch (e.state) {
    case 'queued':
      // queued wake covering N coalesced transcript rows; never a subprocess.
      return t('threads.delivery.queued', { count: e.coalesced_message_count, range });
    case 'running': {
      const elapsed = localizedElapsed(e.started_at, nowMs ?? Date.now(), t);
      return elapsed
        ? t('threads.delivery.runningElapsed', { elapsed, range })
        : t('threads.delivery.running', { range });
    }
    case 'held':
      return t('threads.delivery.held', { range });
    case 'retry_required': {
      const raw = e.current_failure_category;
      if (!raw) return t('threads.delivery.retry', { range });
      const key = FAILURE_CATEGORY_KEYS[raw];
      const category = key ? t(key) : raw.replaceAll('_', ' ');
      return t('threads.delivery.retryCategory', { range, category });
    }
  }
}

function dotClass(state: ReplyDeliveryEntry['state']): string {
  switch (state) {
    case 'queued':
      return 'bg-border-default';
    case 'running':
      return 'bg-info';
    case 'held':
      return 'bg-feedback-success';
    case 'retry_required':
      return 'bg-attention';
  }
}

function stateClass(state: ReplyDeliveryEntry['state']): string {
  switch (state) {
    case 'queued':
      return 'text-text-muted';
    case 'running':
      return 'text-info';
    case 'held':
      return 'text-text-secondary';
    case 'retry_required':
      return 'text-attention-text';
  }
}
