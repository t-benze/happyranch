/**
 * Participants rail ▸ Guests (THR-362 spec §13.2): desktop-app guest seats,
 * listed under the agents in their own group. Each seat shows its label,
 * state, expiry, read position ("read to #n · m new") and last activity, with
 * Copy nudge (joined), New code (invited) and Revoke (not ended).
 *
 * `manageable` is false when the UI is reached through remote access (spec
 * §7.3, decision 13): New code and Revoke are not rendered there, while the
 * seat list and Copy nudge (clipboard only, no request) stay. Read-only data:
 * the seat projection never carries a code or token.
 */
import { useState } from 'react';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import type { Tone } from '@/design-system/patterns/semanticTone';
import { useTranslation } from '@/hooks/i18n';
import type { ThreadGuest } from '@/hooks/threads';
import { guestNewCount, guestNudgeText, guestStateLabel } from './guestSeats';

const STATE_TONE: Record<string, Tone> = {
  invited: 'attention',
  joined: 'positive',
  ended: 'neutral',
};

const SEAT_ACTION =
  'text-caption text-text-secondary hover:text-text-primary focus-visible:ring-ring cursor-pointer rounded-sm font-semibold hover:underline focus-visible:ring-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50';

interface Props {
  threadId: string;
  guests: ThreadGuest[];
  /** Highest message seq in the loaded transcript (for "m new"). */
  maxSeq: number;
  formatTime: (iso: string) => string;
  relativeLabel: (iso: string) => string;
  manageable: boolean;
  /** The seat whose New code / Revoke call is in flight. */
  pendingGuestId: string | null;
  onNewCode: (guest: ThreadGuest) => void;
  onRevoke: (guest: ThreadGuest) => void;
}

export function GuestSeats({
  threadId,
  guests,
  maxSeq,
  formatTime,
  relativeLabel,
  manageable,
  pendingGuestId,
  onNewCode,
  onRevoke,
}: Props): JSX.Element {
  const { t } = useTranslation();
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const copyNudge = (guest: ThreadGuest) => {
    void navigator.clipboard?.writeText(guestNudgeText(threadId));
    setCopiedId(guest.id);
  };
  return (
    <ul className="flex flex-col gap-2">
      {guests.map((g) => {
        const ended = g.state === 'ended';
        const busy = pendingGuestId === g.id;
        const newCount = guestNewCount(g, maxSeq);
        return (
          <li key={g.id} className="text-body-sm flex min-w-0 flex-col gap-0.75 py-0.5">
            <div className="flex min-w-0 items-center gap-2">
              <span aria-hidden="true" className="bg-attention size-1.75 shrink-0 rounded-full" />
              <span className={`text-caption min-w-0 truncate font-mono ${ended ? 'text-text-secondary' : 'text-text-primary'}`}>
                {g.label}
              </span>
              <span className="ml-auto shrink-0">
                <StatusBadge tone={STATE_TONE[g.state] ?? 'neutral'} label={guestStateLabel(g.state, t)} />
              </span>
            </div>
            {!ended && (
              <div className="text-task-meta text-text-secondary flex min-w-0 flex-wrap gap-x-2 gap-y-0.5 pl-3.75">
                <span>{t('threads.page.guest.until', { time: formatTime(g.expires_at) })}</span>
                {g.state === 'joined' && (
                  <span>
                    {g.last_read_seq > 0
                      ? t('threads.page.guest.readTo', { seq: g.last_read_seq, count: newCount })
                      : t('threads.page.guest.notRead', { count: newCount })}
                  </span>
                )}
                {g.state === 'joined' && g.last_seen_at && (
                  <span>{t('threads.page.guest.lastActive', { ago: relativeLabel(g.last_seen_at) })}</span>
                )}
              </div>
            )}
            {!ended && (g.state === 'joined' || manageable) && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-3.75">
                {g.state === 'joined' && (
                  <button
                    type="button"
                    className={SEAT_ACTION}
                    aria-label={t('threads.page.guest.copyNudgeAria', { label: g.label })}
                    onClick={() => copyNudge(g)}
                  >
                    {copiedId === g.id ? t('threads.page.guest.nudgeCopied') : t('threads.page.guest.copyNudge')}
                  </button>
                )}
                {manageable && g.state === 'invited' && (
                  <button
                    type="button"
                    className={SEAT_ACTION}
                    aria-label={t('threads.page.guest.newCodeAria', { label: g.label })}
                    disabled={busy}
                    onClick={() => onNewCode(g)}
                  >
                    {t('threads.page.guest.newCode')}
                  </button>
                )}
                {manageable && (
                  <button
                    type="button"
                    className={`${SEAT_ACTION} hover:text-feedback-danger`}
                    aria-label={t('threads.page.guest.revokeAria', { label: g.label })}
                    disabled={busy}
                    onClick={() => onRevoke(g)}
                  >
                    {t('threads.page.guest.revoke')}
                  </button>
                )}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
