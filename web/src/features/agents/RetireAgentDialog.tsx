/**
 * RetireAgentDialog — Founder confirmation for retiring an active agent.
 *
 * The daemon route is the only eligibility authority: the dialog states the
 * consequences, sends one request, and shows a refusal as the server gave it
 * while the agent stays active. No client-side eligibility rule lives here.
 *
 * A failure that is not a server refusal (no answer, a server fault, 404)
 * leaves the outcome unknown, so the dialog re-reads the roster: an agent that
 * is gone is treated as retired, and nothing is claimed if that read fails.
 */
import { useRef, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useAgentsList, useRetireAgent } from '@/hooks/agents';
import { useTranslation } from '@/hooks/i18n';
import { classifyRetireError, renderAgentError, type RetireErrorView } from './strings';

/** Blocking items listed before the remainder is summarized as a count. */
const SHOWN_CONFLICTS = 8;

interface RetireAgentDialogProps {
  agentName: string;
  displayName: string;
  /** Roster role and team; the manager-team consequence shows for managers only. */
  role: string | null | undefined;
  team: string | null | undefined;
  onClose: () => void;
  /** Where focus goes when the dialog closes; the opener is outside this root. */
  onCloseAutoFocus?: (event: Event) => void;
  /** Called once the retirement is confirmed. */
  onRetired: (agentName: string) => void;
}

export function RetireAgentDialog({
  agentName, displayName, role, team, onClose, onCloseAutoFocus, onRetired,
}: RetireAgentDialogProps): JSX.Element {
  const { t } = useTranslation();
  const retire = useRetireAgent();
  const roster = useAgentsList();
  const [error, setError] = useState<RetireErrorView | null>(null);
  /** What is known about the agent after a failure; `unconfirmed` claims nothing. */
  const [state, setState] = useState<'unchanged' | 'stillListed' | 'unconfirmed'>('unchanged');
  // Covers the request and the roster check after an unknown outcome.
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  // Synchronous latch: a second activation before React re-renders is dropped.
  const inFlight = useRef(false);
  const isManager = role === 'manager';

  const onConfirm = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      await retire.mutateAsync({ agentName });
      onRetired(agentName);
    } catch (err: unknown) {
      const view = classifyRetireError(err);
      if (view.outcome === 'refused') {
        setState('unchanged');
        setError(view);
        return;
      }
      // Unknown outcome: the request may have been applied. Settlement already
      // dropped every earlier roster read, so this one is newer than the request.
      setChecking(true);
      const fresh = await roster.refetch().catch(() => null);
      const listed = fresh === null || fresh.isError || fresh.data === undefined
        ? null
        : fresh.data.agents.some((a) => a.name === agentName);
      if (listed === false) {
        onRetired(agentName);
        return;
      }
      setState(listed === null ? 'unconfirmed' : 'stillListed');
      setError(view);
    } finally {
      inFlight.current = false;
      setChecking(false);
      setBusy(false);
    }
  };

  const hidden = error ? error.conflicts.length - SHOWN_CONFLICTS : 0;

  return (
    <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <DialogContent closeLabel={t('common.close')} onCloseAutoFocus={onCloseAutoFocus}>
        <DialogHeader>
          <DialogTitle>{t('agents.retire.title', { name: displayName })}</DialogTitle>
          <DialogDescription>{t('agents.retire.intro')}</DialogDescription>
        </DialogHeader>
        <ul className="text-text-primary list-disc space-y-1.5 pl-5 text-sm">
          <li>{t('agents.retire.consequence.stops')}</li>
          <li>{t(isManager ? 'agents.retire.consequence.preservedManager' : 'agents.retire.consequence.preserved')}</li>
          <li>{t('agents.retire.consequence.terminated')}</li>
          {isManager && (
            <li>{t('agents.retire.consequence.team', { team: team ?? '—' })}</li>
          )}
        </ul>
        {error !== null && (
          <div role="alert" className="border-border-default bg-danger-soft text-text-primary space-y-1 rounded-md border p-3 text-sm break-words">
            <p className="font-medium">{renderAgentError(error.headline, t)}</p>
            {error.serverMessage !== null && <p>{error.serverMessage}</p>}
            {error.conflicts.length > 0 && (
              <p>
                {t('agents.retire.refusal.blocking', { items: error.conflicts.slice(0, SHOWN_CONFLICTS).join(', ') })}
                {hidden > 0 && ` ${t('agents.retire.refusal.more', { count: hidden })}`}
              </p>
            )}
            {error.members.length > 0 && (
              <p>{t('agents.retire.refusal.members', { items: error.members.join(', ') })}</p>
            )}
            {error.code !== null && (
              <p className="text-text-secondary text-xs">{t('agents.retire.refusal.code', { code: error.code })}</p>
            )}
            <p className="text-text-secondary text-xs">
              {state === 'unchanged' ? t('agents.retire.refusal.unchanged')
                : state === 'stillListed' ? t('agents.retire.outcome.stillListed')
                : t('agents.retire.outcome.unconfirmed')}
            </p>
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
          <Button variant="destructive" loading={busy} onClick={() => void onConfirm()}>
            {checking ? t('agents.retire.checking') : busy ? t('agents.retire.pending') : t('agents.retire.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
