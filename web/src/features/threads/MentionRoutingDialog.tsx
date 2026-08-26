/**
 * THR-198 Slice C — per-thread mention-routing control.
 *
 * Founder-only settings surface (the thread-detail header's ⋯ overflow
 * menu opens this dialog). The switch truthfully renders the server's
 * durable ``threads.mention_routing_enabled`` via the loaded thread record:
 * the page passes the live value as ``enabled`` and the optimistic mutation
 * (``useSetThreadMentionRouting``) flips the cache on success, rolls it
 * back on failure, and treats same-state server no-ops (``idempotent``) as
 * success. While a change is in flight the switch is disabled so a second
 * toggle cannot issue a duplicate mutation.
 *
 * Routing-only: the copy explicitly distinguishes routing (who gets woken)
 * from priority/fairness scheduling. Auth/permission posture is unchanged —
 * the daemon route remains founder-gated exactly as shipped in Slice B.
 */
import { useId, useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useSetThreadMentionRouting } from '@/hooks/threads';
import { THREADS_STRINGS as S } from './strings';

interface Props {
  threadId: string;
  /** Server-derived current state (live thread record, cache-updated). */
  enabled: boolean;
  open: boolean;
  onClose: () => void;
}

function RoutingSwitch({
  value,
  disabled,
  onChange,
  labelledBy,
  ref,
}: {
  value: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
  labelledBy: string;
  ref?: React.Ref<HTMLButtonElement>;
}): JSX.Element {
  return (
    <button
      ref={ref}
      type="button"
      role="switch"
      aria-checked={value}
      aria-labelledby={labelledBy}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={(e) => {
        // Ignore keyboard-synthesized clicks; keyboard activation is handled
        // in onKeyDown so Enter/Space toggle exactly once.
        if (e.detail === 0) return;
        onChange(!value);
      }}
      onKeyDown={(e) => {
        if (
          e.key === 'Enter' ||
          e.key === ' ' ||
          e.key === 'Space' ||
          e.key === 'Spacebar'
        ) {
          e.preventDefault();
          if (!disabled) onChange(!value);
        }
      }}
      className={`inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
        value ? 'bg-accent' : 'bg-bg-raised border-border border'
      } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform ${
          value ? 'translate-x-4' : 'translate-x-0.5'
        }`}
      />
    </button>
  );
}

export function MentionRoutingDialog({ threadId, enabled, open, onClose }: Props): JSX.Element {
  const toggle = useSetThreadMentionRouting(threadId);
  const labelId = useId();
  const switchRef = useRef<HTMLButtonElement>(null);
  const [failed, setFailed] = useState(false);

  const flip = async (next: boolean) => {
    setFailed(false);
    try {
      // Optimistic cache update happens in the hook; idempotent same-state
      // server responses resolve normally (no error surfaced).
      await toggle.mutateAsync({ mention_routing_enabled: next });
    } catch {
      // The hook rolled the cache back, so the switch re-reads the previous
      // server state — show the inline error so the user knows why.
      setFailed(true);
    }
    // While the change was in flight the switch was disabled, which drops
    // keyboard focus; restore it so a keyboard user stays on the control.
    switchRef.current?.focus();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{S.mentionRoutingDialogTitle}</DialogTitle>
          <DialogDescription className="sr-only">
            Choose whether messages that mention participants wake only those
            participants, or whether every message wakes everyone.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex items-start justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1">
              <span id={labelId} className="text-text-primary text-body-sm font-medium">
                {S.mentionRoutingToggleLabel}
              </span>
              <span className="text-text-muted text-body-sm">
                {S.mentionRoutingDescription}
              </span>
            </div>
            <RoutingSwitch
              value={enabled}
              disabled={toggle.isPending}
              onChange={(v) => { void flip(v); }}
              labelledBy={labelId}
              ref={switchRef}
            />
          </div>
          {failed && (
            <p role="alert" className="text-feedback-danger text-body-sm">
              {S.mentionRoutingFailed}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={toggle.isPending}>
            Close
          </Button>
          {toggle.isPending && <span className="text-text-muted text-body-sm">{S.mentionRoutingSaving}</span>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
