/**
 * Desktop-app thread guests (THR-362 slice 4, spec §13.2): pure helpers shared
 * by the participants rail, the invite dialog and the transcript.
 *
 * A guest is a seat, never an agent: its permanent id is `guest:G-n` and its
 * label (e.g. "Codex desktop (guest)") comes from the daemon's seat projection.
 * Nothing here holds or derives a code or token.
 */
import { ApiError } from '@/lib/api';
import type { Locale, MessageKey, MessageParams } from '@/lib/i18n';
import type { ThreadGuest } from '@/hooks/threads';

type Translate = (key: MessageKey, params?: MessageParams) => string;

export const GUEST_SPEAKER_PREFIX = 'guest:';

export function isGuestSpeaker(speaker: string | null | undefined): boolean {
  return !!speaker && speaker.startsWith(GUEST_SPEAKER_PREFIX);
}

/** Seat label by permanent id (`guest:G-n`), for speakers and system events. */
export function guestLabelMap(guests: ThreadGuest[] | undefined): Map<string, string> {
  return new Map((guests ?? []).map((g) => [g.id, g.label]));
}

/**
 * Label for a `guest:` speaker. Falls back to the raw id (never a guessed
 * name) when the seat is not in the projection, e.g. an older daemon.
 */
export function guestSpeakerLabel(speaker: string, labels: Map<string, string>): string {
  return labels.get(speaker) ?? speaker;
}

/**
 * The text Copy nudge puts on the clipboard (spec §4.5). It is pasted into the
 * desktop app as a prompt, like the daemon's English invite prompt, so it is a
 * fixed machine-facing instruction rather than localized UI copy.
 */
export function guestNudgeText(threadId: string): string {
  return `Check HappyRanch thread ${threadId} for new messages and respond.`;
}

/** Seat expiry display: short date and minute in the UI locale (host zone). */
export function guestTime(locale: Locale, iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(locale, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

/**
 * Messages newer than the seat's read position, from the loaded transcript.
 * Only the seat's read scope counts: a from-now seat's floor is
 * `read_from_seq - 1` (the daemon's own read floor), so history before the
 * invite is never "new" even before the guest's first read.
 */
export function guestNewCount(guest: ThreadGuest, maxSeq: number): number {
  const floor = Math.max(0, (guest.read_from_seq ?? 0) - 1);
  return Math.max(0, maxSeq - Math.max(guest.last_read_seq, floor));
}

/** Displayed seat state: a joined seat with an open wait shows "listening". */
export function guestDisplayState(guest: ThreadGuest): string {
  return guest.state === 'joined' && guest.listening === true ? 'listening' : guest.state;
}

const STATE_KEY: Record<string, MessageKey> = {
  invited: 'threads.page.guest.state.invited',
  joined: 'threads.page.guest.state.joined',
  listening: 'threads.page.guest.state.listening',
  ended: 'threads.page.guest.state.ended',
};

export function guestStateLabel(state: string, t: Translate): string {
  const key = STATE_KEY[state];
  // An unknown future state renders verbatim (never a guessed translation).
  return key ? t(key) : state;
}

const CAUSE_KEY: Record<string, MessageKey> = {
  revoked: 'threads.page.system.guestCause.revoked',
  expired: 'threads.page.system.guestCause.expired',
  thread_archived: 'threads.page.system.guestCause.threadArchived',
  invite_lapsed: 'threads.page.system.guestCause.inviteLapsed',
};

export function guestEndCauseLabel(cause: unknown, t: Translate): string {
  const raw = typeof cause === 'string' ? cause : '';
  const key = CAUSE_KEY[raw];
  return key ? t(key) : raw;
}

const ERROR_KEY: Record<string, MessageKey> = {
  guest_seat_limit: 'threads.page.guest.error.seatLimit',
  thread_not_open: 'threads.page.guest.error.threadNotOpen',
  guest_code_not_unused: 'threads.page.guest.error.codeUsed',
  guest_seat_ended: 'threads.page.guest.error.seatEnded',
  guest_not_found: 'threads.page.guest.error.notFound',
};

/**
 * Locale-neutral identity of a failed invite / new-code / revoke call: a
 * catalog key, or the generic key plus the verbatim machine detail. State
 * holds this, never translated text, so a locale switch re-translates it.
 */
export interface GuestErrorKey {
  key: MessageKey;
  detail?: string;
}

export function guestErrorKey(err: unknown): GuestErrorKey {
  if (err instanceof ApiError) {
    const key = err.code ? ERROR_KEY[err.code] : undefined;
    if (key) return { key };
    return { key: 'threads.page.guest.error.generic', detail: err.code ?? `HTTP ${err.status}` };
  }
  return { key: 'threads.page.guest.error.generic', detail: err instanceof Error ? err.message : String(err) };
}

/** Founder-facing message for a stored error identity, in the current locale. */
export function guestErrorText(error: GuestErrorKey, t: Translate): string {
  return error.detail === undefined ? t(error.key) : t(error.key, { detail: error.detail });
}
