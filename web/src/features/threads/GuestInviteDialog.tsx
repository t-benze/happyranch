/**
 * Invite desktop app (THR-362 spec §4.2 / §13.2). Two phases in one dialog:
 *
 *   1. form   — app, seat lifetime, read scope, and the §7.5 warning about
 *               what the guest will read. Confirm creates the seat.
 *   2. prompt — the one-time join prompt (the daemon returns it once and
 *               nothing caches it), a Copy prompt button, the "shown once"
 *               note and New code while the seat is still invited.
 *
 * The rail's New code opens this dialog straight in phase 2 with the
 * replacement prompt (`initialResult`). The prompt lives only in this
 * component's state and is dropped when the dialog closes.
 */
import { useEffect, useId, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { SegmentedControl } from '@/design-system/primitives/SegmentedControl';
import { useInviteGuest, useReplaceGuestCode, type ThreadGuestInviteResponse } from '@/hooks/threads';
import { useTranslation } from '@/hooks/i18n';
import { guestErrorMessage } from './guestSeats';

type AppId = 'codex_desktop' | 'claude_desktop';
type Expires = '1h' | '24h' | '7d';
type Scope = 'whole' | 'from_now';

interface Props {
  threadId: string;
  open: boolean;
  onClose: () => void;
  /** Opens directly on the prompt phase (rail New code). */
  initialResult?: ThreadGuestInviteResponse | null;
}

export function GuestInviteDialog({ threadId, open, onClose, initialResult = null }: Props): JSX.Element {
  const { t } = useTranslation();
  const invite = useInviteGuest(threadId);
  const replace = useReplaceGuestCode(threadId);
  const [app, setApp] = useState<AppId>('codex_desktop');
  const [expires, setExpires] = useState<Expires>('24h');
  const [scope, setScope] = useState<Scope>('whole');
  const [result, setResult] = useState<ThreadGuestInviteResponse | null>(initialResult);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [copied, setCopied] = useState(false);
  const latch = useRef(false);
  const appId = useId();
  const expiresId = useId();
  const scopeId = useId();
  const promptId = useId();

  useEffect(() => {
    if (!open) return;
    // Each opening starts clean; the previous prompt is never shown again.
    setApp('codex_desktop');
    setExpires('24h');
    setScope('whole');
    setResult(initialResult);
    setError(null);
    setCopied(false);
  }, [open, initialResult]);

  const run = async (op: () => Promise<ThreadGuestInviteResponse>) => {
    if (latch.current) return;
    latch.current = true;
    setPending(true);
    setError(null);
    try {
      setResult(await op());
      setCopied(false);
    } catch (err) {
      setError(guestErrorMessage(err, t));
    } finally {
      latch.current = false;
      setPending(false);
    }
  };

  const submit = (e?: React.FormEvent) => {
    e?.preventDefault();
    void run(() => invite.mutateAsync({ app_id: app, expires, from_now: scope === 'from_now' }));
  };

  const newCode = () => {
    if (!result) return;
    void run(() => replace.mutateAsync({ guestId: result.guest.id }));
  };

  const copy = () => {
    if (!result) return;
    void navigator.clipboard?.writeText(result.prompt);
    setCopied(true);
  };

  const title = initialResult && result
    ? t('threads.dialog.guest.newCodeTitle', { label: result.guest.label })
    : t('threads.dialog.guest.title');

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent closeLabel={t('common.close')}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="text-caption text-text-secondary">
            {t('threads.dialog.guest.description')}
          </DialogDescription>
        </DialogHeader>
        {result ? (
          <div className="flex min-w-0 flex-col gap-3">
            <div className="flex items-center justify-between gap-2">
              <label htmlFor={promptId} className="text-caption text-text-primary font-semibold">
                {t('threads.dialog.guest.promptLabel')}
              </label>
              <Button type="button" variant="secondary" size="sm" onClick={copy}>
                {copied ? t('threads.dialog.guest.copied') : t('threads.dialog.guest.copyPrompt')}
              </Button>
            </div>
            <textarea
              id={promptId}
              readOnly
              value={result.prompt}
              rows={9}
              onFocus={(e) => e.currentTarget.select()}
              className="bg-surface-sunken border-border-default text-text-primary text-mono-sm focus-visible:ring-ring w-full min-w-0 resize-none rounded-sm border p-2.5 font-mono focus-visible:ring-2 focus-visible:outline-none"
            />
            <p className="text-caption text-attention-text bg-attention-soft rounded-sm px-2.5 py-2">
              {t('threads.dialog.guest.shownOnce')}
            </p>
            <p className="text-caption text-text-secondary">{t('threads.dialog.guest.onlyActs')}</p>
            {error && <p className="text-caption text-feedback-danger" role="alert">{error}</p>}
            <DialogFooter>
              {result.guest.state === 'invited' && (
                <Button type="button" variant="ghost" onClick={newCode} disabled={pending}>
                  {pending ? t('threads.dialog.guest.newCodePending') : t('threads.page.guest.newCode')}
                </Button>
              )}
              <Button type="button" onClick={onClose}>{t('threads.dialog.guest.done')}</Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={submit} className="flex min-w-0 flex-col gap-3">
            <ChoiceField label={t('threads.dialog.guest.appLabel')} labelId={appId}>
              <SegmentedControl<AppId>
                aria-labelledby={appId}
                value={app}
                onValueChange={setApp}
                options={[
                  { value: 'codex_desktop', label: t('threads.dialog.guest.app.codex') },
                  { value: 'claude_desktop', label: t('threads.dialog.guest.app.claude') },
                ]}
              />
            </ChoiceField>
            <ChoiceField label={t('threads.dialog.guest.expiresLabel')} labelId={expiresId}>
              <SegmentedControl<Expires>
                aria-labelledby={expiresId}
                value={expires}
                onValueChange={setExpires}
                options={[
                  { value: '1h', label: t('threads.dialog.guest.expires.1h') },
                  { value: '24h', label: t('threads.dialog.guest.expires.24h') },
                  { value: '7d', label: t('threads.dialog.guest.expires.7d') },
                ]}
              />
            </ChoiceField>
            <ChoiceField label={t('threads.dialog.guest.scopeLabel')} labelId={scopeId}>
              <SegmentedControl<Scope>
                aria-labelledby={scopeId}
                value={scope}
                onValueChange={setScope}
                options={[
                  { value: 'whole', label: t('threads.dialog.guest.scope.whole') },
                  { value: 'from_now', label: t('threads.dialog.guest.scope.fromNow') },
                ]}
              />
            </ChoiceField>
            <p className="text-caption text-attention-text bg-attention-soft rounded-sm px-2.5 py-2">
              {t('threads.dialog.guest.warning')}
            </p>
            {error && <p className="text-caption text-feedback-danger" role="alert">{error}</p>}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
              <Button type="submit" disabled={pending}>
                {pending ? t('threads.dialog.guest.pending') : t('threads.dialog.guest.confirm')}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** A labelled single-choice row: the label names the radiogroup (FormField's
 *  `htmlFor` targets a form control, which a radiogroup is not). */
function ChoiceField({ label, labelId, children }: { label: string; labelId: string; children: React.ReactNode }): JSX.Element {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span id={labelId} className="text-label text-text-muted font-medium tracking-wide">{label}</span>
      {children}
    </div>
  );
}
