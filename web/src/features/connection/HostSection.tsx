/**
 * Host management inside the Connection dialog (THR-387 S6-W3; EM M3).
 *
 * Linux Host: `host.status` + `host.stateChanged`, and the v1 host commands
 * (enable, turn off, create invite, approve / reject the requesting device,
 * select next device, revoke). Native owns every effect, prompt and record:
 * this view never renders invite bytes, comparison phrases or device
 * identities, never edits the device list optimistically and re-reads after
 * every action. Turn off and Revoke first show an in-dialog confirmation, then
 * native shows its own system consent.
 *
 * Hidden when `host.status` answers `unsupportedCommand` (Mac: no host role).
 */
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import {
  callConnection,
  failureOf,
  parseActionResult,
  parseEnableAdmission,
  subscribeEnableFinished,
  parseHostSnapshot,
  UNKNOWN_FAILURE,
  type ActionResult,
  type ConnectionFailure,
  type HostCommand,
  type EnableFinished,
} from '@/lib/native/connection';
import { ACTION_LABEL, hostView, outcomeView } from './connectionModel';
import { Actions, Hint, Notice, OutcomeNotice, Section, ToneBadge, WRAP, formatTime, joinSentences } from './parts';
import { useHostSnapshot } from './useConnectionReads';

type Confirmable = 'host.turnOff' | 'host.revokeDevice';

interface HostOutcome {
  command: HostCommand;
  /** Device label captured when the action was requested. */
  name: string | null;
  result?: ActionResult;
  failure?: ConnectionFailure;
  /** The follow-up re-read failed. */
  read?: ConnectionFailure;
}

const RECORD_BOUND: readonly HostCommand[] = ['host.approveDevice', 'host.rejectDevice', 'host.revokeDevice'];

export function HostSection(): JSX.Element | null {
  const { t, locale } = useTranslation();
  const reads = useHostSnapshot();
  const [pending, setPending] = useState<HostCommand | null>(null);
  const [outcome, setOutcome] = useState<HostOutcome | null>(null);
  // F2: an action whose effect is unknown, kept apart from the transient
  // outcome notice (Check again clears that). It holds the successful
  // observation count at the moment the action returned; only a NEWER
  // successful observation (host.status read or host.stateChanged) ends it.
  const [uncertainSince, setUncertainSince] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<{ command: Confirmable; name: string | null } | null>(null);
  const confirmCancelRef = useRef<HTMLButtonElement>(null);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const invocation = useRef<{ id?: string; early?: EnableFinished; consumed: boolean } | null>(null);
  const receive = useRef<(event: EnableFinished) => void>(() => {});
  receive.current = (event) => {
    const attempt = invocation.current;
    if (!attempt || attempt.consumed) return;
    if (!attempt.id) {
      attempt.early ??= event;
      return;
    }
    if (event.operationId !== attempt.id) return;
    attempt.consumed = true;
    attempt.early = undefined;
    setUncertainSince(reads.invalidate());
    setPending(null);
    setOutcome({ command: 'host.enable', name: null, failure: event.failure ?? UNKNOWN_FAILURE });
    void reads.refresh();
  };
  useEffect(() => {
    const off = subscribeEnableFinished((event) => receive.current(event));
    return () => {
      invocation.current = null;
      off?.();
    };
  }, []);

  const { state } = reads;
  if (state.kind === 'unsupported') return null;

  const run = async (command: HostCommand, name: string | null): Promise<void> => {
    if (pending !== null) return;
    setConfirm(null);
    setPending(command);
    setOutcome(null);
    const attempt = command === 'host.enable' ? { consumed: false } as {
      id?: string; early?: EnableFinished; consumed: boolean;
    } : null;
    invocation.current = attempt;
    const before = reads.revision();
    let next: HostOutcome;
    try {
      const raw = await callConnection(command);
      if (command === 'host.nextDevice') {
        const snapshot = parseHostSnapshot(raw);
        setPending(null);
        // Applied only if no newer observation arrived meanwhile.
        if (snapshot) reads.observe(snapshot, before);
        else setOutcome({ command, name, failure: UNKNOWN_FAILURE });
        return;
      }
      const admission = command === 'host.enable' ? parseEnableAdmission(raw) : null;
      if (attempt && invocation.current === attempt) {
        attempt.id = admission?.operationId;
        if (attempt.id && attempt.early) receive.current(attempt.early);
        attempt.early = undefined;
        if (attempt.consumed) return;
      }
      const result = command === 'host.enable' ? admission?.result : parseActionResult(raw);
      next = result ? { command, name, result } : { command, name, failure: UNKNOWN_FAILURE };
    } catch (error) {
      if (attempt?.consumed) return;
      next = { command, name, failure: failureOf(error) };
    }
    if (attempt && invocation.current !== attempt) return;
    if (next.result?.effect === 'unknown' || next.failure?.effect === 'unknown') setUncertainSince(reads.observedCount());
    setPending(null);
    setOutcome(next);
    // Re-read after every mutation; never edit the list optimistically.
    const fresh = await reads.refresh();
    if (!fresh) setOutcome((current) => (current === next ? { ...next, read: UNKNOWN_FAILURE } : current));
  };

  const checkAgain = (): void => {
    setOutcome((current) => current?.failure?.code === 'enablePreparationFailed' ? current : null);
    void reads.refresh();
  };

  const snapshot = state.kind === 'ready' ? state.snapshot : state.kind === 'failed' ? state.snapshot : null;
  // An uncertain action with no successful observation since: the retained snapshot proves nothing.
  const uncertain = uncertainSince !== null && reads.observed <= uncertainSince;
  const view = snapshot ? hostView(snapshot, uncertain) : null;
  const busy = pending !== null;

  const button = (
    command: HostCommand,
    label: string,
    options: { variant?: 'default' | 'secondary' | 'outline' | 'destructiveOutline'; name?: string | null; ariaName?: string } = {},
  ): JSX.Element => (
    <Button
      key={command}
      size="sm"
      variant={options.variant ?? 'secondary'}
      className={WRAP}
      aria-disabled={busy || undefined}
      aria-busy={pending === command || undefined}
      aria-label={options.ariaName ? `${label}: ${options.ariaName}` : undefined}
      data-command={command}
      onClick={(event) => {
        if (busy) return;
        if (command === 'host.turnOff' || command === 'host.revokeDevice') {
          openerRef.current = event.currentTarget;
          setConfirm({ command, name: options.name ?? null });
          requestAnimationFrame(() => confirmCancelRef.current?.focus());
          return;
        }
        void run(command, options.name ?? null);
      }}
    >
      {label}
    </Button>
  );

  const outcomeNode = (() => {
    if (!outcome) return null;
    const action = t(ACTION_LABEL[outcome.command]);
    const params = { action, name: outcome.name ?? '' };
    const parts: JSX.Element[] = [];
    const ov = outcome.result
      ? outcomeView(outcome.command, { result: outcome.result }, null)
      : outcome.failure
        ? outcomeView(outcome.command, { failure: outcome.failure }, null)
        : null;
    if (ov) {
      const current = outcome.command === 'host.revokeDevice' ? (snapshot?.selected?.label ?? null) : (snapshot?.pending?.label ?? null);
      const stillApplies = !RECORD_BOUND.includes(outcome.command) || (current !== null && current === outcome.name);
      parts.push(
        <OutcomeNotice
          key="outcome"
          testId="connection-host-outcome"
          view={{ ...ov, retry: ov.retry && stillApplies,
            checkAgain: outcome.failure?.code === 'enablePreparationFailed' ? false : ov.checkAgain }}
          params={params}
          result={outcome.result}
          onCheckAgain={checkAgain}
          onRetry={() => void run(outcome.command, outcome.name)}
          retryLabel={t('connection.client.tryAgainAction', { action })}
        />,
      );
      if (outcome.failure?.code === 'enablePreparationFailed') parts.push(
        <Button key="check" size="sm" variant="outline" className={WRAP} onClick={checkAgain}>
          {t('connection.host.checkCurrentStatus')}
        </Button>,
      );
      if (ov.retry && !stillApplies) parts.push(<Hint key="gone">{t('connection.host.hRecordGone', { name: outcome.name ?? '' })}</Hint>);
    }
    // Only while the newest read still failed: a later successful observation supersedes it.
    if (outcome.read && state.kind === 'failed') {
      parts.push(
        <Notice key="read" tone="attention" announce>
          <p>{joinSentences([t('connection.client.failedAction', { action: t('connection.client.checkAgain') }), t('connection.err.unavailable')], locale)}</p>
        </Notice>,
      );
    }
    return parts;
  })();

  return (
    <Section title={t('connection.host.hostEyebrow')} testId="connection-host" aside={view && <ToneBadge tone={view.tone} label={t(view.badge)} />}>
      {state.kind === 'loading' && <p className="text-text-secondary text-sm">{t('connection.host.loading')}</p>}
      {state.kind === 'failed' && !snapshot && (
        <OutcomeNotice
          testId="connection-host-read"
          view={outcomeView('host.status', { failure: state.failure }, null) ?? { lines: [], tone: 'attention', checkAgain: true, retry: false, hints: [], components: false }}
          params={{ action: t('connection.client.checkAgain'), name: '' }}
          onCheckAgain={checkAgain}
        />
      )}
      {view && snapshot && (
        <div className="space-y-3">
          <h4 className="text-base font-semibold break-words">{t(view.title, { name: view.requester ?? '' })}</h4>
          <p className="text-text-secondary text-sm break-words">{t(view.lead)}</p>
          {view.stale && <Notice tone="attention">{t('connection.host.hStale')}</Notice>}
          {outcomeNode}
          {confirm && (
            <Notice tone="danger" testId="connection-host-confirm">
              <p className="font-semibold">
                {t(confirm.command === 'host.turnOff' ? 'connection.host.hConfirmTurnOffTitle' : 'connection.host.hConfirmRevokeTitle', { name: confirm.name ?? '' })}
              </p>
              <p className="mt-1">{t(confirm.command === 'host.turnOff' ? 'connection.host.hConfirmTurnOff' : 'connection.host.hConfirmRevoke')}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button
                  ref={confirmCancelRef}
                  size="sm"
                  variant="outline"
                  className={WRAP}
                  onClick={() => {
                    setConfirm(null);
                    requestAnimationFrame(() => openerRef.current?.focus());
                  }}
                >
                  {t('connection.host.confirmCancel')}
                </Button>
                <Button
                  size="sm"
                  variant="destructiveOutline"
                  className={WRAP}
                  aria-disabled={busy || undefined}
                  data-command={`confirm:${confirm.command}`}
                  onClick={() => void run(confirm.command, confirm.name)}
                >
                  {t(confirm.command === 'host.turnOff' ? 'connection.host.hTurnOff' : 'connection.host.hRevokeThis')}
                </Button>
              </div>
              <p className="mt-2 text-xs">{t('connection.client.systemAsks')}</p>
            </Notice>
          )}
          {view.invite === 'issued' && (
            <Notice tone="progress">
              {snapshot.expiresAt && <p>{t('connection.client.inviteExpiresAt', { time: formatTime(snapshot.expiresAt, locale) })}</p>}
              <p className="mt-1 text-xs">{t('connection.host.hInviteNoCancel')}</p>
            </Notice>
          )}
          {view.invite === 'expired' && <Notice tone="attention">{t('connection.host.hInviteExpired')}</Notice>}
          {view.invite === 'used' && <Notice tone="neutral">{t('connection.host.hInviteUsed')}</Notice>}
          {!confirm && (view.enable || view.approve || view.createInvite || view.checkAgain) && (
            <>
              <Actions>
                {view.enable && button('host.enable', t('connection.host.hTurnOn'), { variant: 'default' })}
                {view.approve && button('host.approveDevice', t('connection.host.hApprove'), { variant: 'default', name: view.requester, ariaName: view.requester ?? undefined })}
                {view.approve && button('host.rejectDevice', t('connection.host.hReject'), { variant: 'destructiveOutline', name: view.requester, ariaName: view.requester ?? undefined })}
                {view.createInvite && button('host.createInvite', t('connection.host.hAddDevice'), { variant: 'default' })}
                {view.checkAgain && (
                  <Button size="sm" variant="outline" className={WRAP} onClick={checkAgain}>
                    {t('connection.client.checkAgain')}
                  </Button>
                )}
              </Actions>
              {(view.enable || view.approve || view.createInvite) && <Hint>{t('connection.client.systemAsks')}</Hint>}
            </>
          )}
          {view.hints.map((hint) => (
            <Hint key={hint}>{t(hint)}</Hint>
          ))}
          {view.manage && (
            <div className="space-y-2" data-testid="connection-host-devices">
              <h4 className="text-sm font-semibold">{t('connection.host.hDevicesTitle')}</h4>
              {snapshot.devices === null ? (
                <Notice tone="attention">{t('connection.host.hDevicesUnknown')}</Notice>
              ) : snapshot.devices.length === 0 ? (
                <p className="text-sm">{t('connection.host.hDevicesEmpty')}</p>
              ) : (
                <>
                  {/* Labels are not identities and can repeat: rows carry no actions. */}
                  <ul className="space-y-1 text-sm">
                    {snapshot.devices.map((device, index) => (
                      <li key={index} className="break-words">
                        {device.label}
                      </li>
                    ))}
                  </ul>
                  <Hint>{t('connection.host.hDevicesHint')}</Hint>
                  <p className="text-sm break-words">
                    <span className="text-text-secondary">{t('connection.host.hSelectedDevice')}: </span>
                    <strong>{snapshot.selected?.label ?? t('connection.host.hNoneSelected')}</strong>
                  </p>
                  {!confirm && (
                    <Actions>
                      {button('host.nextDevice', t(snapshot.selected ? 'connection.host.hNextDevice' : 'connection.host.hSelectDevice'))}
                      {snapshot.selected &&
                        button('host.revokeDevice', t('connection.host.hRevokeSelected'), {
                          variant: 'destructiveOutline',
                          name: snapshot.selected.label,
                          ariaName: snapshot.selected.label,
                        })}
                    </Actions>
                  )}
                </>
              )}
            </div>
          )}
          {view.turnOff && !confirm && <Actions>{button('host.turnOff', t('connection.host.hTurnOff'), { variant: 'destructiveOutline' })}</Actions>}
          <Hint>{t('connection.host.hNativeNote')}</Hint>
        </div>
      )}
    </Section>
  );
}
