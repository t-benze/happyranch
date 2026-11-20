import { Ban, Check, TriangleAlert } from 'lucide-react';
import { IdentityName } from '@/shared/identities/IdentityName';
import type { ResponderStatusEntry } from '@/lib/api/types';
import { useTranslation } from '@/hooks/i18n';
import { localizedElapsed, type DeliveryTranslator } from './ReplyDeliveryStrip';

export type ResponderStripAlign = 'start' | 'center' | 'end';

const ALIGN_CLASS: Record<ResponderStripAlign, string> = {
  start: 'justify-start',
  center: 'justify-center',
  end: 'justify-end',
};

export function ResponderStatusStrip({
  statuses,
  nowMs,
  align = 'start',
}: {
  statuses: ResponderStatusEntry[];
  nowMs?: number;
  /** Row alignment: `end` under a founder bubble, `center` under a system
   *  event, `start` under an agent card (THR-376 mockup .rcpt). */
  align?: ResponderStripAlign;
}): JSX.Element | null {
  const { t } = useTranslation();
  // THR-376 receipt pills (mockup .rc): replied / working / declined / failed
  // (plus the neutral founder-abort). Queued stays off the strip: the tail
  // TypingBubble is its only surface. Working is shown here as a receipt pill
  // beside the tail TypingBubble, as the mockup's live state does; both read
  // the same existing responder_status data.
  const shown = statuses.filter(
    (s) =>
      s.status === 'replied' ||
      s.status === 'working' ||
      s.status === 'declined' ||
      s.status === 'failed',
  );
  if (shown.length === 0) return null;
  const now = nowMs ?? Date.now();
  return (
    <div className={`mt-1.5 flex flex-wrap items-center gap-1.5 ${ALIGN_CLASS[align]}`}>
      {shown.map((s) => (
        <span
          key={s.agent_name}
          data-receipt={isAborted(s) ? 'aborted' : s.status}
          className={`text-caption inline-flex max-w-full min-w-0 items-center gap-1.25 rounded-pill py-px pr-2 ${pillClass(s)}`}
        >
          <ReceiptIcon s={s} />
          <span className="text-mono-sm min-w-0 truncate font-mono"><IdentityName canonicalId={s.agent_name} /></span>
          <span className="shrink-0">{statusLabel(s, now, t)}</span>
        </span>
      ))}
    </div>
  );
}

function ReceiptIcon({ s }: { s: ResponderStatusEntry }): JSX.Element {
  if (isAborted(s)) return <Ban aria-hidden="true" className="size-2.75 shrink-0" />;
  switch (s.status) {
    case 'working':
      return (
        <span
          aria-hidden="true"
          className="border-info-soft border-t-info size-2.25 shrink-0 animate-spin rounded-full border-2"
        />
      );
    case 'replied':
      return <Check aria-hidden="true" className="size-2.75 shrink-0" />;
    case 'declined':
      return <Ban aria-hidden="true" className="size-2.75 shrink-0" />;
    default:
      return <TriangleAlert aria-hidden="true" className="size-2.75 shrink-0" />;
  }
}

// A founder-initiated abort is persisted as a `failed` invocation with
// decline_reason='founder_aborted' (the backend reap marker) — categorized
// as 'infra_fail'. But an abort is a deliberate cancellation, NOT an infra
// failure, so it must read as a NEUTRAL 'aborted' state, never red "reply
// failed…". Branch on this marker BEFORE the category/status switch. Genuine
// failures (any other decline_reason) keep their danger styling untouched.
function isAborted(s: ResponderStatusEntry): boolean {
  return s.decline_reason === 'founder_aborted';
}

function statusLabel(s: ResponderStatusEntry, nowMs: number, t: DeliveryTranslator): string {
  if (isAborted(s)) return t('threads.responder.aborted');
  switch (s.status) {
    case 'queued':
      return t('threads.responder.queued');
    case 'working': {
      const elapsed = localizedElapsed(s.started_at, nowMs, t);
      return elapsed
        ? t('threads.responder.workingElapsed', { elapsed })
        : t('threads.responder.working');
    }
    case 'replied':
      return t('threads.responder.replied');
    case 'declined':
    case 'failed':
      // Category-distinguished so the founder can tell the four terminal
      // causes apart. Falls back to today's generic label when category is
      // null (older/replied data or a row PR-A didn't classify).
      return (
        terminalLabel(s, t) ??
        t(s.status === 'declined' ? 'threads.responder.declined' : 'threads.responder.failed')
      );
  }
}

// Maps the persisted failure/decline category to a founder-readable label.
// Returns null when there is no category to distinguish (caller falls back
// to the generic 'declined'/'failed' label).
function terminalLabel(s: ResponderStatusEntry, t: DeliveryTranslator): string | null {
  switch (s.category) {
    case 'declined':
      return t('threads.responder.declined');
    case 'no_callback':
      return t('threads.responder.noCallback');
    case 'no_callback_after_reprompt':
      return t('threads.responder.noCallbackAfterReprompt');
    case 'infra_fail': {
      // Surface the return code when the backend embedded one (rc=N), matching
      // the daemon's own infra-signature parse; otherwise a bare infra label.
      const rc = s.decline_reason?.match(/rc=(\d+)/i)?.[1];
      return rc ? t('threads.responder.infraRc', { rc }) : t('threads.responder.infra');
    }
    default:
      return null;
  }
}

// Receipt pill tone per state (THR-376 mockup .rc.ok / .work / .declined /
// .failed). The replied (accent-text) and failed (danger) tokens keep the
// historical class names. A founder abort stays neutral, never danger.
function pillClass(s: ResponderStatusEntry): string {
  if (isAborted(s)) return 'border-border-default bg-surface-raised text-text-secondary border pl-1.5';
  switch (s.status) {
    case 'working':
      return 'bg-info-soft text-info pl-1.5';
    case 'replied':
      return 'text-accent-text pl-0';
    case 'declined':
      return 'bg-attention-soft text-attention-text pl-1.5';
    case 'failed':
      return 'bg-danger-soft text-danger pl-1.5';
    default:
      return 'border-border-default bg-surface-raised text-text-secondary border pl-1.5';
  }
}
