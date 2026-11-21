/**
 * Connection dialog body (THR-387 S6-W3; handoff v2.4 §2-§5, §7; C1-C6).
 *
 * Composed into the desktop dialog host by `routes.tsx` (`renderConnectionBody`).
 * State sources:
 * - the ONE #1774 app-status store (`useNativeAppStatus`): active target,
 *   state, candidate phase/deadline, incompatible side, local service and
 *   `access_unsaved`. No second store holds target, generation or candidate.
 * - v1 `client.*` / `host.*` reads (`useConnectionReads`), held in component
 *   state only.
 *
 * Rules:
 * - Closing the dialog is not Cancel or Disconnect: it only unmounts this
 *   body. Native keeps the attempt; reopening re-reads `app.status`,
 *   `client.status` and `host.status` and replays no action.
 * - A candidate is shown with its phase/deadline/steps while the old target
 *   stays active ("You're still using ..."). It is never labelled Connected:
 *   Ready to switch needs the explicit Switch.
 * - Switch and Disconnect ask the contextual owner gate over EVERY owner
 *   first (effects `switchTarget` / `disconnect`), then publish the owner
 *   summary and await native's receipt, freeze saves, call
 *   `client.commitSwitch` / `client.disconnect`, and release the freeze when
 *   native answers. Every refusal gets named copy; nothing retries on its own.
 * - While an attempt exists its section comes first, with Cancel/Switch on
 *   top, so Cancel is visible on open at 380x280 CSS px; the owner gate
 *   offers Cancel too (M13).
 */
import { Settings } from 'lucide-react';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { DialogTitle } from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import type { MessageParams } from '@/lib/i18n';
import { useNativeAppStatus } from '@/lib/native/hooks';
import { awaitOwnersReceipt, holdOwnerSaves } from '@/lib/native/owners';
import { getAppStatus, refreshAppStatus, subscribeAppStatus } from '@/lib/native/target';
import type { AppState } from '@/lib/native/appStatus';
import {
  callConnection,
  failureOf,
  parseActionResult,
  UNKNOWN_FAILURE,
  type ActionResult,
  type ClientCommand,
  type ConnectionFailure,
} from '@/lib/native/connection';
import { ownerContextParams, useOwnerGate, useOwnerRecords, useOwnerSnapshotReader } from '@/shared/owners';
import {
  ACTION_LABEL,
  cancelHint,
  candidateCancellable,
  candidateView,
  displayLabel,
  isAttemptStage,
  outcomeView,
  refusalView,
  stepRows,
  targetView,
  type CommitAction,
} from './connectionModel';
import { Actions, Hint, Notice, OutcomeNotice, Section, Steps, ToneBadge, WRAP, formatSeconds, formatTime, joinSentences } from './parts';
import { HostSection } from './HostSection';
import { useClientSnapshot, useHealth } from './useConnectionReads';

export interface ConnectionDialogBodyProps {
  headingRef: RefObject<HTMLHeadingElement>;
  onOpenSettings: () => void;
}

interface ClientOutcome {
  command: ClientCommand;
  args: Record<string, string>;
  /** Raw native record label at the action (localized at render time). */
  raw: string | null;
  result?: ActionResult;
  failure?: ConnectionFailure;
}

interface CommitState {
  kind: CommitAction;
  running: boolean;
  failure: ConnectionFailure | null;
  /** Native result of a disconnect (its outcome copy). */
  result?: ActionResult;
}

/**
 * The explicit Switch/Disconnect the user chose, bound to what they chose it
 * for (F1). Switch binds the candidate (kind/id) and the target still in
 * use; Disconnect binds the active target. `stale` is sticky: once native
 * state stops matching (Cancel, a replacement candidate, a phase change, a
 * new target) the intent can never be spent, even if a later state looks
 * the same again.
 */
interface CommitIntent {
  kind: CommitAction;
  candidate: string | null;
  target: string | null;
  stale: boolean;
}

// kind + id only: names are display text and may be relocalized by a locale
// change, which must not drop the user's choice. Candidate ids are fresh per attempt.
const identity = (item: { kind: string; id: string } | null | undefined): string | null => (item ? `${item.kind}:${item.id}` : null);

function intentFor(kind: CommitAction, app: AppState | null): CommitIntent | null {
  if (!app) return null;
  if (kind === 'switch') {
    if (app.candidate?.phase !== 'ready_to_switch') return null;
    return { kind, candidate: identity(app.candidate), target: identity(app.target), stale: false };
  }
  if (!app.target) return null;
  return { kind, candidate: null, target: identity(app.target), stale: false };
}

/** True while native state still describes what the intent was chosen for. */
function intentHolds(intent: CommitIntent, app: AppState | null): boolean {
  if (intent.stale || !app) return false;
  if (identity(app.target) !== intent.target) return false;
  if (intent.kind === 'switch') return app.candidate?.phase === 'ready_to_switch' && identity(app.candidate) === intent.candidate;
  return true;
}

const ATTEMPT_COMMANDS: readonly ClientCommand[] = ['client.acceptInvite', 'client.connectSaved', 'client.retry', 'client.cancelAttempt'];

export function ConnectionDialogBody({ headingRef, onOpenSettings }: ConnectionDialogBodyProps): JSX.Element {
  const { t, locale } = useTranslation();
  const app = useNativeAppStatus();
  const client = useClientSnapshot(app);
  const records = useOwnerRecords();
  const readOwners = useOwnerSnapshotReader();
  const { requestOwnerTransition } = useOwnerGate();
  const connectRef = useRef<HTMLHeadingElement>(null);
  const alive = useRef(true);
  const [pending, setPending] = useState<ClientCommand | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [outcome, setOutcome] = useState<ClientOutcome | null>(null);
  const [commit, setCommit] = useState<CommitState | null>(null);
  const [rechecking, setRechecking] = useState(false);
  const committing = useRef(false);
  const intent = useRef<CommitIntent | null>(null);

  // Reopen re-reads the native status (a read; no mutation is replayed).
  useEffect(() => {
    alive.current = true;
    void refreshAppStatus();
    return () => {
      alive.current = false;
    };
  }, []);

  // Every native status change re-checks the pending Switch/Disconnect intent
  // (while the owner gate is open and during the receipt wait): a mismatch
  // drops it for good.
  useEffect(
    () =>
      subscribeAppStatus(() => {
        const held = intent.current;
        if (held && !intentHolds(held, getAppStatus())) held.stale = true;
      }),
    [],
  );

  const snapshot = client.snapshot;
  const stage = snapshot?.stage ?? null;
  const remoteConnected = app?.target?.kind === 'remote' && app.state === 'ready' && stage === 'connected';
  const health = useHealth(remoteConnected);

  const savedWord = t('connection.client.savedComputer');
  const label = (raw: string): string => displayLabel(raw, savedWord, locale);

  const recheck = (): void => {
    setRechecking(true);
    void refreshAppStatus().finally(() => {
      if (alive.current) setRechecking(false);
    });
    void client.refresh();
  };

  const runClient = async (command: ClientCommand, args: Record<string, string> = {}, raw: string | null = null): Promise<void> => {
    const cancel = command === 'client.cancelAttempt';
    // Cancel is busy-exempt; every other mutation runs one at a time.
    if (!cancel && (pending !== null || committing.current)) return;
    // Cancel (or any new attempt) ends the Switch the user chose for the old candidate.
    if (ATTEMPT_COMMANDS.includes(command) && intent.current?.kind === 'switch') intent.current.stale = true;
    if (cancel) setCancelling(true);
    else setPending(command);
    setOutcome(null);
    let next: ClientOutcome;
    try {
      const result = parseActionResult(await callConnection(command, args));
      next = result ? { command, args, raw, result } : { command, args, raw, failure: UNKNOWN_FAILURE };
    } catch (error) {
      next = { command, args, raw, failure: failureOf(error) };
    }
    if (!alive.current) return;
    if (cancel) setCancelling(false);
    else setPending(null);
    setOutcome(next);
    // Re-read native state after every action; never edit lists optimistically.
    void client.refresh();
    void refreshAppStatus();
  };

  const runCommit = async (chosen: CommitIntent): Promise<void> => {
    if (committing.current) return;
    const { kind } = chosen;
    // Revalidate the chosen candidate/target against the newest native state
    // after the owner gate and after every wait, right before anything is
    // sent. A changed choice sends nothing and needs a fresh explicit action.
    const stillChosen = (): boolean => intent.current === chosen && intentHolds(chosen, getAppStatus());
    const drop = (): void => {
      if (intent.current === chosen) intent.current = null;
      if (alive.current) setCommit({ kind, running: false, failure: { code: 'intent_changed', effect: 'none', recovery: 'refresh' } });
    };
    if (!stillChosen()) return drop();
    committing.current = true;
    setCommit({ kind, running: true, failure: null });
    setOutcome(null);
    let failure: ConnectionFailure | null = null;
    let result: ActionResult | undefined;
    // S6-W2b: native must have acknowledged the current owner summary first.
    const admitted = await awaitOwnersReceipt(readOwners());
    if (!admitted) {
      failure = { code: 'receipt', effect: 'none', recovery: 'refresh' };
    } else {
      const release = holdOwnerSaves();
      try {
        if (!stillChosen()) {
          committing.current = false;
          return drop();
        }
        const parsed = parseActionResult(await callConnection(kind === 'switch' ? 'client.commitSwitch' : 'client.disconnect'));
        if (!parsed) failure = UNKNOWN_FAILURE;
        else if (parsed.effect === 'unknown') failure = { code: 'outcomeUnknown', effect: 'unknown', recovery: 'nativeRecovery' };
        else result = parsed;
      } catch (error) {
        failure = failureOf(error);
      } finally {
        release();
      }
    }
    if (intent.current === chosen) intent.current = null;
    committing.current = false;
    if (!alive.current) return;
    setCommit({ kind, running: false, failure, result });
    void client.refresh();
    void refreshAppStatus();
    if (!failure && kind === 'switch') headingRef.current?.focus();
  };

  const requestCommit = (kind: CommitAction): void => {
    if (committing.current || pending !== null) return;
    setCommit(null);
    // Bind the choice to the candidate/target the user saw (newest native state).
    const chosen = intentFor(kind, getAppStatus());
    if (!chosen) {
      intent.current = null;
      setCommit({ kind, running: false, failure: { code: kind === 'switch' ? 'target_starting' : 'intent_changed', effect: 'none', recovery: 'refresh' } });
      return;
    }
    const previous = intent.current;
    intent.current = chosen;
    requestOwnerTransition({
      effect: kind === 'switch' ? 'switchTarget' : 'disconnect',
      // A target commit affects every Main and Settings owner.
      affects: () => true,
      proceed: () => void runCommit(chosen),
      // Keep editing, or a repeat while a gate is already open: the open
      // gate keeps its own intent.
      cancel: () => {
        if (intent.current === chosen) intent.current = previous;
      },
    });
  };

  const chooseAnother = (): void => {
    connectRef.current?.scrollIntoView({ block: 'start' });
    connectRef.current?.focus({ preventScroll: true });
  };

  const header = (
    <header className="border-border-default flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b py-3 pr-14 pl-4 md:pl-6">
      <DialogTitle
        ref={headingRef}
        tabIndex={-1}
        className="font-display focus-visible:ring-accent-default w-fit max-w-full rounded-sm text-lg break-words focus:outline-none focus-visible:ring-2"
      >
        {t('desktopDialogs.connection.title')}
      </DialogTitle>
      <Button type="button" size="sm" variant="outline" className={WRAP} onClick={onOpenSettings}>
        <Settings aria-hidden="true" />
        {t('desktopDialogs.connection.openSettings')}
      </Button>
    </header>
  );

  if (!app) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        {header}
        <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
          <p role="status" className="text-text-secondary text-sm">
            {t('connection.client.loading')}
          </p>
        </div>
      </div>
    );
  }

  const target = app.target;
  const targetName = target?.name ?? '';
  const view = targetView(app);
  const candidate = app.candidate;
  const cv = candidate ? candidateView(candidate, target !== null) : null;
  const inFlight = candidateCancellable(candidate);
  const attemptStage = stage !== null && isAttemptStage(stage);
  const canStart = !inFlight && !attemptStage && (stage === null || stage === 'idle' || stage === 'failed' || stage === 'connected');
  const busy = pending !== null || commit?.running === true;
  const committedRemote = target?.kind === 'remote' && candidate === null && stage === 'connected';

  const outcomeParams = (o: ClientOutcome): MessageParams => ({
    action: t(ACTION_LABEL[o.command], { name: candidate?.name ?? '' }),
    name: o.raw === null ? '' : label(o.raw),
  });
  const outcomeNode = (where: 'candidate' | 'connect' | 'access'): JSX.Element | null => {
    if (!outcome) return null;
    const home =
      outcome.command === 'client.retrySave'
        ? 'access'
        : ATTEMPT_COMMANDS.includes(outcome.command) && candidate
          ? 'candidate'
          : 'connect';
    if (home !== where) return null;
    const ov = outcome.result
      ? outcomeView(outcome.command, { result: outcome.result }, snapshot?.savedAccess ?? null)
      : outcome.failure
        ? outcomeView(outcome.command, { failure: outcome.failure }, snapshot?.savedAccess ?? null)
        : null;
    if (!ov) return null;
    const params = outcomeParams(outcome);
    const recordBound = outcome.command === 'client.connectSaved' || outcome.command === 'client.removeSaved';
    const stillListed = !recordBound || !!snapshot?.computers?.some((c) => c.computerRef === outcome.args.computerRef);
    return (
      <>
        <OutcomeNotice
          view={{ ...ov, retry: ov.retry && stillListed }}
          params={params}
          onCheckAgain={recheck}
          onRetry={() => void runClient(outcome.command, outcome.args, outcome.raw)}
          retryLabel={t('connection.client.tryAgainAction', { action: params.action as string })}
        />
        {ov.retry && !stillListed && <Hint>{t('connection.client.recordGone', { name: params.name as string })}</Hint>}
      </>
    );
  };

  const refusalNode = (kind: CommitAction): JSX.Element | null => {
    if (!commit || commit.kind !== kind) return null;
    if (commit.running) {
      return (
        <p role="status" className="text-text-secondary text-sm">
          {t(kind === 'switch' ? 'connection.candidate.switching' : 'connection.client.working')}
        </p>
      );
    }
    if (!commit.failure) {
      if (kind === 'disconnect' && commit.result) {
        const ov = outcomeView('client.disconnect', { result: commit.result }, snapshot?.savedAccess ?? null);
        return ov ? <OutcomeNotice view={ov} params={{ action: t('connection.client.disconnect'), name: '' }} /> : null;
      }
      return null;
    }
    const rv = refusalView(commit.failure, kind);
    const params = { name: kind === 'switch' ? (candidate?.name ?? '') : targetName, active: targetName };
    const owners = rv.showOwners ? records.filter((owner) => owner.status !== 'clean') : [];
    return (
      <Notice tone={rv.tone} announce testId={`connection-refusal-${kind}`}>
        <p>
          {joinSentences(
            [t(rv.lead), t(rv.reason, params), ...(kind === 'switch' && target ? [t('connection.candidate.stillUsing', { active: targetName })] : [])],
            locale,
          )}
        </p>
        {owners.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs">
            {owners.map((owner) => (
              <li key={owner.id}>
                {t(
                  owner.status === 'pending'
                    ? 'owners.gate.ownerPending'
                    : owner.status === 'unknown'
                      ? 'owners.gate.ownerUnknown'
                      : owner.status === 'failed'
                        ? 'owners.gate.ownerFailed'
                        : 'owners.gate.ownerDirty',
                  ownerContextParams(owner, t),
                )}
              </li>
            ))}
          </ul>
        )}
        {rv.tone === 'attention' && (
          <div className="mt-2">
            <Button size="sm" variant="outline" className={WRAP} onClick={recheck}>
              {t('connection.client.checkAgain')}
            </Button>
          </div>
        )}
      </Notice>
    );
  };

  const deadline = candidate?.deadline ?? (attemptStage ? (snapshot?.expiresAt ?? null) : null);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {header}
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 md:p-6" data-testid="connection-body">
        {/* 1 · Connection attempt first, actions on top: Cancel/Switch are visible on open at 380x280. */}
        {candidate && cv && (
          <Section title={t('connection.candidate.label')} testId="connection-candidate" aside={<ToneBadge tone={cv.tone} label={t(cv.badge)} />}>
            <h4 className="text-base font-semibold break-words" data-testid="connection-candidate-title">
              {t(cv.title, { name: candidate.name })}
            </h4>
            <p className="text-sm break-words">{t(cv.lead, { name: candidate.name, active: targetName })}</p>
            {(cv.switchable || cv.cancel || cv.retry) && (
              <Actions>
                {cv.switchable && (
                  <Button
                    size="sm"
                    className={WRAP}
                    aria-disabled={busy || undefined}
                    data-command="client.commitSwitch"
                    onClick={() => requestCommit('switch')}
                  >
                    {t('connection.candidate.switch', { name: candidate.name })}
                  </Button>
                )}
                {cv.cancel && (
                  <Button
                    size="sm"
                    variant="outline"
                    className={WRAP}
                    aria-busy={cancelling || undefined}
                    data-command="client.cancelAttempt"
                    onClick={() => void runClient('client.cancelAttempt')}
                  >
                    {t(cancelling ? 'connection.candidate.cancelling' : 'connection.candidate.cancel')}
                  </Button>
                )}
                {cv.retry && (
                  <Button size="sm" className={WRAP} aria-disabled={busy || undefined} data-command="client.retry" onClick={() => void runClient('client.retry')}>
                    {t('connection.client.retryAttempt')}
                  </Button>
                )}
              </Actions>
            )}
            {refusalNode('switch')}
            {outcomeNode('candidate')}
            {cv.cancel && <Hint>{t(cancelHint(stage))}</Hint>}
            {cv.switchable && target && <Hint>{t('connection.candidate.switchHint', { active: targetName })}</Hint>}
            {deadline && !cv.finished && (
              <p className="text-attention-text text-sm break-words" data-testid="connection-deadline">
                {t('connection.client.inviteExpiresAt', { time: formatTime(deadline, locale) })}
              </p>
            )}
            {inFlight && stage !== null && <Steps rows={stepRows(stage, snapshot?.savedAccess ?? 'unknown')} />}
            {inFlight && stage === 'validating' && <Hint>{t('connection.client.leadValidating')}</Hint>}
            {inFlight && stage === 'waitingApproval' && <Hint>{t('connection.client.leadWaiting')}</Hint>}
            {inFlight && stage === 'saving' && <Hint>{t('connection.client.leadSaving')}</Hint>}
          </Section>
        )}

        {/* 2 · Current connection (the attempt's lead also names it). */}
        <Section title={t('connection.current.label')} testId="connection-current" aside={<ToneBadge tone={view.tone} label={t(view.badge)} />}>
          <h4 className="text-base font-semibold break-words" data-testid="connection-target-title">
            {t(view.title, { name: targetName })}
          </h4>
          <p className="text-text-secondary text-sm break-words">{t(view.body, { name: targetName })}</p>
          {view.compatibility && app.incompatible?.host_contract && (
            <Hint>
              {t('connection.compat.range', {
                name: targetName,
                min: app.incompatible.host_contract.min_client,
                current: app.incompatible.host_contract.current,
                client: app.incompatible.client_contract,
              })}
            </Hint>
          )}
          {(view.recheck || view.compatibility?.chooseAnother || (view.disconnect && !inFlight)) && (
            <Actions>
              {view.recheck && (
                <Button size="sm" variant={view.compatibility ? 'default' : 'secondary'} className={WRAP} disabled={rechecking} onClick={recheck}>
                  {t(rechecking ? 'connection.rechecking' : 'connection.recheck')}
                </Button>
              )}
              {view.compatibility?.chooseAnother && (
                <Button size="sm" variant="outline" className={WRAP} onClick={chooseAnother}>
                  {t('connection.chooseAnother')}
                </Button>
              )}
              {view.disconnect && !inFlight && (
                <Button
                  size="sm"
                  variant="destructiveOutline"
                  className={WRAP}
                  aria-disabled={busy || undefined}
                  data-command="client.disconnect"
                  onClick={() => requestCommit('disconnect')}
                >
                  {t('connection.client.disconnect')}
                </Button>
              )}
            </Actions>
          )}
          {refusalNode('disconnect')}
          {/* A dropped Switch stays named even after its attempt is gone. */}
          {!candidate && refusalNode('switch')}
          {view.health && remoteConnected && (
            <div className="space-y-2" data-testid="connection-health">
              <dl className="grid gap-1 text-sm sm:grid-cols-2">
                {(
                  [
                    ['connection.client.healthAccess', health.health?.access],
                    ['connection.client.healthRuntime', health.health?.runtime],
                    ['connection.client.healthRoute', health.health?.route],
                  ] as const
                ).map(([key, value]) => (
                  <div key={key} className="flex flex-wrap gap-x-2">
                    <dt className="text-text-secondary">{t(key)}</dt>
                    <dd>{t(healthValueKey(health.health?.observation === 'unavailable' ? undefined : value))}</dd>
                  </div>
                ))}
                <div className="flex flex-wrap gap-x-2">
                  <dt className="text-text-secondary">{t('connection.client.healthChecked')}</dt>
                  <dd>
                    {health.health?.observedAt && health.health.observation !== 'unavailable'
                      ? formatSeconds(health.health.observedAt, locale)
                      : t('connection.client.unknownValue')}
                  </dd>
                </div>
              </dl>
              {health.health?.observation === 'stale' && <Hint>{t('connection.client.healthStale')}</Hint>}
              {health.health?.access === 'accessDenied' && <Notice tone="danger">{t('connection.err.accessDenied')}</Notice>}
              {health.health?.access === 'revokedConfirmed' && <Notice tone="danger">{t('connection.err.revoked')}</Notice>}
              {health.failure && (
                <OutcomeNotice
                  view={outcomeView('client.health', { failure: health.failure }, null) ?? { lines: [], tone: 'attention', checkAgain: false, retry: false, hints: [], components: false }}
                  params={{ action: t('connection.client.checkHealth'), name: '' }}
                />
              )}
              <Actions>
                <Button size="sm" variant="outline" className={WRAP} disabled={health.checking} onClick={health.check}>
                  {t('connection.client.checkHealth')}
                </Button>
              </Actions>
            </div>
          )}
        </Section>

        {/* 3 · Access wasn't saved (kept separate from save-failure copy, C3). */}
        {app.access_unsaved && (
          <Notice tone="attention" testId="connection-access-unsaved">
            <p className="font-semibold">{t('connection.access.title')}</p>
            <p className="mt-1">{t('connection.access.body')}</p>
            {stage === 'connected' && (snapshot?.savedAccess === 'needsPermission' || snapshot?.savedAccess === 'unknown') && (
              <p className="mt-1 text-xs">
                {t(snapshot.savedAccess === 'needsPermission' ? 'connection.client.saveLocked' : 'connection.client.saveUnknown')}
              </p>
            )}
            {stage === 'connected' ? (
              <div className="mt-2 space-y-1">
                <Button
                  size="sm"
                  className={WRAP}
                  aria-disabled={busy || undefined}
                  aria-busy={pending === 'client.retrySave' || undefined}
                  data-command="client.retrySave"
                  onClick={() => void runClient('client.retrySave')}
                >
                  {t(pending === 'client.retrySave' ? 'connection.access.retrying' : 'connection.access.retry')}
                </Button>
                <p className="text-xs">{t('connection.client.systemAsks')}</p>
              </div>
            ) : (
              <p className="mt-1 text-xs">{t('connection.client.lastNotSaved')}</p>
            )}
          </Notice>
        )}
        {outcomeNode('access')}

        {/* 4 · Connect another computer: invite + saved computers. */}
        <Section ref={connectRef} title={t('connection.connect.title')} testId="connection-connect">
          <p className="text-text-secondary text-sm break-words">{t('connection.client.leadIdle')}</p>
          {canStart ? (
            <Actions>
              <Button
                size="sm"
                variant={target ? 'secondary' : 'default'}
                className={WRAP}
                aria-disabled={busy || undefined}
                aria-busy={pending === 'client.acceptInvite' || undefined}
                data-command="client.acceptInvite"
                onClick={() => void runClient('client.acceptInvite')}
              >
                {t('connection.client.acceptInvite')}
              </Button>
            </Actions>
          ) : (
            <Hint>{t('connection.connect.busyHint')}</Hint>
          )}
          {outcomeNode('connect')}
          <h4 className="text-sm font-semibold">{t('connection.client.savedTitle')}</h4>
          {snapshot === null ? (
            client.readFailure ? (
              <OutcomeNotice
                view={outcomeView('client.status', { failure: client.readFailure }, null) ?? { lines: [], tone: 'attention', checkAgain: true, retry: false, hints: [], components: false }}
                params={{ action: t('connection.client.checkAgain'), name: '' }}
                onCheckAgain={recheck}
              />
            ) : (
              <p role="status" className="text-text-secondary text-sm">
                {t('connection.client.loading')}
              </p>
            )
          ) : snapshot.computers && snapshot.computers.length > 0 ? (
            <>
              <ul className="space-y-2" data-testid="connection-saved">
                {snapshot.computers.map((computer) => {
                  const name = label(computer.label);
                  // "current" only for the committed v2 remote target: native points
                  // activeComputerRef at an attempt's record before any commit (F3).
                  const current = committedRemote && computer.computerRef === snapshot.activeComputerRef;
                  const args = { computerRef: computer.computerRef };
                  return (
                    <li key={computer.computerRef} className="border-border-default flex flex-wrap items-center gap-2 rounded-md border p-3">
                      <span className="min-w-0 flex-1 text-sm break-words">
                        <strong className="font-semibold">{name}</strong>
                        {current && <span className="text-text-secondary"> · {t('connection.client.currentComputer')}</span>}
                      </span>
                      {canStart && !current && (
                        <span className="flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            className={WRAP}
                            aria-disabled={busy || undefined}
                            aria-label={`${t('connection.client.connectSaved')}: ${name}`}
                            data-command="client.connectSaved"
                            onClick={() => void runClient('client.connectSaved', args, computer.label)}
                          >
                            {t('connection.client.connectSaved')}
                          </Button>
                          <Button
                            size="sm"
                            variant="destructiveOutline"
                            className={WRAP}
                            aria-disabled={busy || undefined}
                            aria-label={`${t('connection.client.removeSaved')}: ${name}`}
                            data-command="client.removeSaved"
                            onClick={() => void runClient('client.removeSaved', args, computer.label)}
                          >
                            {t('connection.client.removeSaved')}
                          </Button>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              <Hint>{t('connection.client.savedLabelsHint')}</Hint>
              {snapshot.savedAccess === 'unknown' && <Notice tone="attention">{t('connection.client.savedUnknown')}</Notice>}
            </>
          ) : snapshot.computers === null || snapshot.savedAccess === 'unknown' ? (
            <Notice tone="attention">{t('connection.client.savedUnknownAll')}</Notice>
          ) : (
            <p className="text-sm">{t('connection.client.savedEmpty')}</p>
          )}
          {snapshot?.savedAccess === 'needsPermission' && <Notice tone="attention">{t('connection.client.savedLocked')}</Notice>}
          {snapshot?.savedAccess === 'removalIncomplete' && (
            <Notice tone="attention" testId="connection-removal-incomplete">
              {t('connection.err.removalIncomplete')}
            </Notice>
          )}
          <Hint>{t('connection.client.nativeDialogs')}</Hint>
        </Section>

        {/* 5 · Host management (hidden without a host role). */}
        <HostSection />
      </div>
    </div>
  );
}

function healthValueKey(
  value: string | undefined,
):
  | 'connection.client.unknownValue'
  | 'connection.client.accessAllowed'
  | 'connection.client.accessDenied'
  | 'connection.client.accessRevoked'
  | 'connection.client.runtimeAvailable'
  | 'connection.client.runtimeOffline'
  | 'connection.client.runtimeRestarting'
  | 'connection.client.routeDirect'
  | 'connection.client.routeRelay' {
  switch (value) {
    case 'allowed':
      return 'connection.client.accessAllowed';
    case 'accessDenied':
      return 'connection.client.accessDenied';
    case 'revokedConfirmed':
      return 'connection.client.accessRevoked';
    case 'available':
      return 'connection.client.runtimeAvailable';
    case 'offline':
      return 'connection.client.runtimeOffline';
    case 'restarting':
      return 'connection.client.runtimeRestarting';
    case 'direct':
      return 'connection.client.routeDirect';
    case 'relay':
      return 'connection.client.routeRelay';
    default:
      return 'connection.client.unknownValue';
  }
}
