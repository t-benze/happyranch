/**
 * Settings → HappyRanch on this computer (THR-387 S7-L2; handoff v2.4 §6,
 * corrections C5 and C6).
 *
 * One page for this computer's local service: status, Start/Stop, the
 * default-off keep-running switch with Repair/Turn off, update status and an
 * ALWAYS-VISIBLE service-only Restart (THR-371). Native owns every effect:
 * fresh inspection, Cancel-default consent, the single mutation slot (busy
 * refusal) and reconciliation. The page only shows what native reports.
 *
 * - Checking until the first `local.status`; a failed or unreadable read is
 *   Unknown with Check again, never Off.
 * - A refused or unknown action keeps its exact named result. Nothing is
 *   retried or replayed; Check again / Recheck only re-read.
 * - C5: whenever a remote target is selected the page stays available and
 *   says these controls affect this computer, not that Host.
 * - C6: after Start/Restart native rechecks this computer's readiness and
 *   compatibility and returns it as the result's `readmission`. The page shows
 *   the service result, the readiness check and the compatibility check
 *   separately, ending in Ready or a named mismatch/unreachable/unconfirmed.
 *   Under a remote selection `readmission` is the only source (app.status
 *   describes the remote Host and is never read as local compatibility).
 *   Under local selection app.status stays the readiness source and a
 *   disagreement with `readmission` is named, not silently resolved.
 *   Recheck / Check again only re-read; they never repeat Restart.
 */
import { RotateCcw } from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import type { Tone } from '@/design-system/patterns/semanticTone';
import { Button } from '@/design-system/primitives/Button';
import { useTranslation } from '@/hooks/i18n';
import type { MessageKey, MessageParams } from '@/lib/i18n';
import type { AppState } from '@/lib/native/appStatus';
import { useNativeAppStatus } from '@/lib/native/hooks';
import { appStatusReadInFlight, getAppStatus, refreshAppStatus } from '@/lib/native/target';
import {
  callLocal,
  localFailureOf,
  parseLocalActionResult,
  parseLocalServiceResult,
  parseUpdateStatus,
  type KeepRunning,
  type LocalFailure,
  type LocalMutation,
  type LocalReadmission,
  type LocalServiceSnapshot,
  type UpdateStatus,
} from './localService';

/** Long actions wrap at compact size / 200% text instead of overflowing. */
const WRAP = 'h-auto max-w-full whitespace-normal text-left break-words';

type Outcome =
  /** `readmission`: Start/Restart only; null once superseded by a local Recheck. */
  | { kind: 'done'; command: LocalMutation; readmission: LocalReadmission | null }
  | { kind: 'failed'; command: LocalMutation; failure: LocalFailure };

interface LocalView {
  service: LocalServiceSnapshot | null;
  /** Failure of the latest `local.status` read (shown as Unknown). */
  readFailed: boolean;
  update: UpdateStatus | null;
  pending: LocalMutation | null;
  outcome: Outcome | null;
  /** A previous action's effect is unknown: only Check again until reconciled. */
  unresolved: boolean;
}

const INITIAL: LocalView = { service: null, readFailed: false, update: null, pending: null, outcome: null, unresolved: false };

const PENDING_KEY: Record<LocalMutation, MessageKey> = {
  'local.start': 'settings.local.pending.start',
  'local.stop': 'settings.local.pending.stop',
  'local.restart': 'settings.local.pending.restart',
  'local.repair': 'settings.local.pending.repair',
  'local.turnOff': 'settings.local.pending.turnOff',
  'local.keepRunning.set': 'settings.local.pending.keepRunning',
};

const ACTION_KEY: Record<LocalMutation, MessageKey> = {
  'local.start': 'settings.local.action.start',
  'local.stop': 'settings.local.action.stop',
  'local.restart': 'settings.local.action.restart',
  'local.repair': 'settings.local.action.repair',
  'local.turnOff': 'settings.local.action.turnOff',
  'local.keepRunning.set': 'settings.local.action.keepRunning',
};

/** Named refusal copy; anything else falls back by effect. */
const REFUSAL_KEY: Record<string, MessageKey> = {
  permissionDenied: 'settings.local.refusal.cancelled',
  busy: 'settings.local.refusal.busy',
  unavailable: 'settings.local.refusal.unavailable',
  owner_save_pending: 'settings.local.refusal.ownerSavePending',
  owner_save_unknown: 'settings.local.refusal.ownerSaveUnknown',
  non_get_pending: 'settings.local.refusal.nonGetPending',
};

function useLocalService(): {
  view: LocalView;
  refresh: () => Promise<void>;
  act: (command: LocalMutation, enabled?: boolean) => Promise<void>;
  supersedeReadmission: (outcome: Outcome) => void;
} {
  const [view, setView] = useState<LocalView>(INITIAL);
  const mounted = useRef(true);
  const reads = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const readUpdate = useCallback(async (): Promise<UpdateStatus> => {
    try {
      return parseUpdateStatus(await callLocal('local.update.status')) ?? 'unknown';
    } catch {
      return 'unknown';
    }
  }, []);

  const refresh = useCallback(async (): Promise<void> => {
    const sequence = ++reads.current;
    let service: LocalServiceSnapshot | null = null;
    try {
      service = parseLocalServiceResult(await callLocal('local.status'));
    } catch {
      service = null;
    }
    const update = await readUpdate();
    if (!mounted.current || sequence !== reads.current) return;
    setView((prev) => ({
      ...prev,
      service: service ?? prev.service,
      readFailed: service === null,
      update,
      // Only a fresh, fully known inspection reconciles an unknown effect.
      unresolved: prev.unresolved && !(service && service.running !== 'unknown' && service.keepRunning !== 'unknown' && !service.busy),
    }));
  }, [readUpdate]);

  const act = useCallback(
    async (command: LocalMutation, enabled?: boolean): Promise<void> => {
      setView((prev) => ({ ...prev, pending: command, outcome: null }));
      let outcome: Outcome;
      let service: LocalServiceSnapshot | null = null;
      try {
        const raw = await callLocal(command, command === 'local.keepRunning.set' ? { enabled: enabled === true } : undefined);
        const result = parseLocalActionResult(raw, command);
        service = result?.service ?? null;
        outcome = result
          ? { kind: 'done', command, readmission: result.readmission }
          : { kind: 'failed', command, failure: { code: 'outcomeUnknown', effect: 'unknown', recovery: 'nativeRecovery' } };
      } catch (error) {
        outcome = { kind: 'failed', command, failure: localFailureOf(error) };
      }
      const unknown = outcome.kind === 'failed' && outcome.failure.effect !== 'none';
      reads.current += 1; // An older in-flight read must not overwrite this result.
      if (!mounted.current) return;
      setView((prev) => ({
        ...prev,
        pending: null,
        outcome,
        service: service ?? prev.service,
        readFailed: service ? false : prev.readFailed,
        unresolved: unknown || prev.unresolved,
      }));
      if (outcome.kind === 'done') {
        const update = await readUpdate();
        if (mounted.current) setView((prev) => ({ ...prev, update }));
      }
    },
    [readUpdate],
  );

  // A local-selection Recheck that obtained a fresh local app.status replaces
  // the earlier readmission instead of being compared with it. Only the same
  // result the Recheck was started from is superseded (a newer action wins).
  const supersedeReadmission = useCallback((outcome: Outcome): void => {
    setView((prev) =>
      prev.outcome === outcome && prev.outcome.kind === 'done' && prev.outcome.readmission ? { ...prev, outcome: { ...prev.outcome, readmission: null } } : prev,
    );
  }, []);

  return { view, refresh, act, supersedeReadmission };
}

/**
 * Recheck's app.status read. True only when a local status was published
 * after this read was issued: its own successfully parsed reply, or a newer
 * `app.stateChanged`. `refreshAppStatus()` keeps the cached status when a read
 * is rejected, times out or is unreadable, so its return value is not proof;
 * an unchanged status object means nothing fresh arrived. A read already in
 * flight may predate the click, so it is settled first and not counted.
 * While this is pending, and after a failure, the readmission result and its
 * Recheck stay as they were.
 */
async function readFreshLocalStatus(): Promise<boolean> {
  if (appStatusReadInFlight()) await refreshAppStatus();
  const before = getAppStatus();
  await refreshAppStatus();
  const after = getAppStatus();
  return after !== null && after !== before && after.target?.kind === 'local';
}

/**
 * Does native admit the `local.*` commands on this document? One `local.status`
 * probe per mount: `unsupportedCommand` (e.g. a shell without S7-L2, or no
 * bridge) is `false`; any other answer, including a refusal, is `true`, so the
 * page stays reachable to show its own Unknown/Check again state. `null`
 * while the probe is in flight.
 */
export function useLocalCommandsAdmitted(): boolean | null {
  const [admitted, setAdmitted] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    callLocal('local.status').then(
      () => live && setAdmitted(true),
      (error: unknown) => live && setAdmitted(localFailureOf(error).code !== 'unsupportedCommand'),
    );
    return () => {
      live = false;
    };
  }, []);
  return admitted;
}

export function LocalServicePage(): JSX.Element {
  const { t } = useTranslation();
  const app = useNativeAppStatus();
  const { view, refresh, act, supersedeReadmission } = useLocalService();
  const remoteHost = app?.target?.kind === 'remote' ? app.target.name : null;
  const localSelected = app?.target?.kind === 'local';

  // Read on open, and again whenever native reports a local running change
  // while no action of ours is waiting (e.g. a supervisor observation).
  const appRunning = app?.local.running;
  useEffect(() => {
    if (view.pending) return;
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-read on native local change only
  }, [appRunning]);

  // Re-read only. Under a remote selection app.status describes that Host,
  // so only this computer's local.status / local.update.status are read.
  const outcome = view.outcome;
  const recheck = useCallback(async () => {
    if (!localSelected) {
      await refresh();
      return;
    }
    await Promise.all([refresh(), outcome ? readFreshLocalStatus().then((fresh) => fresh && supersedeReadmission(outcome)) : refreshAppStatus()]);
  }, [refresh, localSelected, outcome, supersedeReadmission]);

  const service = view.service;
  const foreground = service?.mode === 'foreground';
  const unsupported = !!service && (!service.supported || !['service', 'appProcess'].includes(service.mode));
  const known = !!service && !view.readFailed;
  const blocked = !!view.pending || view.unresolved || !!service?.busy || unsupported || !known;
  const running = view.readFailed ? 'unknown' : service?.running;
  const updateReady = view.update === 'restart_required';

  return (
    <div className="max-w-2xl min-w-0 space-y-6 p-4 break-words sm:p-8" data-testid="settings-local">
      <div className="space-y-2">
        <h2 className="font-display text-lg font-semibold">{t('settings.local.title')}</h2>
        <p className="text-text-secondary text-sm">{t('settings.local.description')}</p>
        {remoteHost && (
          <p className="border-border-default bg-info-soft text-text-primary rounded-sm border p-3 text-sm" data-testid="local-scope-note">
            {t('desktopDialogs.settings.localScope', { host: remoteHost })}
          </p>
        )}
      </div>

      <IncompatibleLocalNotice app={app} />

      <Row
        testId="local-service-row"
        title={t('settings.local.service.title')}
        badge={<ServiceBadge running={running} view={view} foreground={foreground} />}
      >
        <p className="text-text-secondary text-sm">{serviceHint(t, view, running, foreground)}</p>
        {view.pending && <PendingNotice command={view.pending} />}
        <div className="flex flex-wrap gap-2">
          {running === 'stopped' && (
            <Button size="sm" className={WRAP} disabled={blocked} onClick={() => void act('local.start')} data-testid="local-start">
              {t('settings.local.action.start')}
            </Button>
          )}
          {running === 'running' && !foreground && (
            <Button size="sm" variant="secondary" className={WRAP} disabled={blocked} onClick={() => void act('local.stop')} data-testid="local-stop">
              {t('settings.local.action.stop')}
            </Button>
          )}
          {/* THR-371: always visible; gated only by capability and busy state. */}
          <Button
            size="sm"
            variant={updateReady ? 'default' : 'outline'}
            className={WRAP}
            disabled={blocked || running === 'starting' || running === 'unknown'}
            onClick={() => void act('local.restart')}
            data-testid="local-restart"
          >
            <RotateCcw aria-hidden="true" />
            {t('settings.local.action.restart')}
          </Button>
          {(view.readFailed || view.unresolved || running === 'unknown') && (
            <Button size="sm" variant="outline" className={WRAP} disabled={!!view.pending} onClick={() => void recheck()}>
              {t('settings.local.checkAgain')}
            </Button>
          )}
        </div>
        <p className="text-text-secondary text-xs">{t('settings.local.restart.scope')}</p>
      </Row>

      {view.outcome && (
        <OutcomeNotice outcome={view.outcome} app={app} remoteHost={remoteHost} onRecheck={() => void recheck()} />
      )}

      <KeepRunningRow
        value={(service?.mode ?? app?.local.mode) === 'appProcess' ? 'unavailable' : view.readFailed ? 'unknown' : service ? service.keepRunning : 'checking'}
        known={known}
        unsupported={unsupported}
        disabled={blocked}
        onSet={(enabled) => void act('local.keepRunning.set', enabled)}
        onRepair={() => void act('local.repair')}
        onTurnOff={() => void act('local.turnOff')}
      />

      <Row testId="local-update-row" title={t('settings.local.update.title')} badge={<UpdateBadge update={view.update} />}>
        <p className="text-text-secondary text-sm">{t(updateHintKey(view.update))}</p>
      </Row>
    </div>
  );
}

function Row({ title, badge, testId, children }: { title: string; badge: ReactNode; testId: string; children: ReactNode }): JSX.Element {
  return (
    <section data-testid={testId} className="border-border-default bg-surface-raised min-w-0 space-y-3 rounded-sm border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-text-primary min-w-0 text-sm font-semibold break-words">{title}</h3>
        {badge}
      </div>
      {children}
    </section>
  );
}

function Badge({ tone, labelKey, dot = false }: { tone: Tone; labelKey: MessageKey; dot?: boolean }): JSX.Element {
  const { t } = useTranslation();
  return <StatusBadge tone={tone} label={t(labelKey)} dot={dot} />;
}

function ServiceBadge({ running, view, foreground }: { running: LocalServiceSnapshot['running'] | undefined; view: LocalView; foreground: boolean }): JSX.Element {
  if (view.pending === 'local.restart') return <Badge tone="info" labelKey="settings.local.state.restarting" dot />;
  if (view.pending === 'local.start') return <Badge tone="info" labelKey="settings.local.state.starting" dot />;
  if (view.pending === 'local.stop') return <Badge tone="info" labelKey="settings.local.state.stopping" dot />;
  if (!view.service && !view.readFailed) return <Badge tone="neutral" labelKey="settings.local.state.checking" dot />;
  if (foreground) return <Badge tone="neutral" labelKey="settings.local.state.foreground" />;
  if (view.outcome?.kind === 'failed' && view.outcome.command === 'local.restart' && view.outcome.failure.effect !== 'none') {
    return <Badge tone="danger" labelKey="settings.local.state.restartFailed" />;
  }
  switch (running) {
    case 'running':
      return <Badge tone="positive" labelKey="settings.local.state.running" />;
    case 'starting':
      return <Badge tone="info" labelKey="settings.local.state.starting" dot />;
    case 'stopped':
      return <Badge tone="neutral" labelKey="settings.local.state.stopped" />;
    default:
      return <Badge tone="attention" labelKey="settings.local.state.unknown" dot />;
  }
}

function serviceHint(t: (key: MessageKey, params?: MessageParams) => string, view: LocalView, running: string | undefined, foreground: boolean): string {
  if (!view.service && !view.readFailed) return t('settings.local.service.checking');
  if (foreground) return t('settings.local.service.foreground');
  if (view.service?.mode === 'appProcess' && !view.service.supported) return t('settings.local.service.external');
  if (view.service && !view.service.supported) return t('settings.local.service.unsupported');
  if (view.unresolved) return t('settings.local.service.unresolved');
  if (view.service?.busy && !view.pending) return t('settings.local.service.busy');
  switch (running) {
    case 'running':
      return t('settings.local.service.running');
    case 'starting':
      return t('settings.local.service.starting');
    case 'stopped':
      return t('settings.local.service.stopped');
    default:
      return t('settings.local.service.unknown');
  }
}

function PendingNotice({ command }: { command: LocalMutation }): JSX.Element {
  const { t } = useTranslation();
  return (
    <div role="status" className="border-border-default bg-info-soft text-text-primary rounded-sm border p-3 text-sm" data-testid="local-pending">
      <p>{t(PENDING_KEY[command])}</p>
      {command !== 'local.start' && <p className="mt-1 text-xs">{t('settings.local.pending.confirm')}</p>}
    </div>
  );
}

const NOTICE_CLASS: Record<'ok' | 'progress' | 'attention' | 'danger', string> = {
  ok: 'border-border-default bg-accent-soft text-text-primary',
  progress: 'border-border-default bg-info-soft text-text-primary',
  attention: 'border-feedback-warning/40 bg-feedback-warning/5 text-text-primary',
  danger: 'border-tier-red bg-feedback-danger/10 text-tier-red',
};

function Notice({ tone, children, testId }: { tone: keyof typeof NOTICE_CLASS; children: ReactNode; testId: string }): JSX.Element {
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} data-testid={testId} data-tone={tone} className={`rounded-sm border p-3 text-sm break-words ${NOTICE_CLASS[tone]}`}>
      {children}
    </div>
  );
}

/** Before any action: the selected local service itself is the incompatible side. */
function IncompatibleLocalNotice({ app }: { app: AppState | null }): JSX.Element | null {
  const { t } = useTranslation();
  if (app?.target?.kind !== 'local' || app.state !== 'incompatible' || app.incompatible?.side !== 'host') return null;
  return (
    <Notice tone="attention" testId="local-incompatible">
      <p className="font-semibold">{t('settings.local.c6.serviceOld.title')}</p>
      <p className="mt-1">{t('settings.local.c6.serviceOld.body')}</p>
    </Notice>
  );
}

type Step = 'done' | 'current' | 'failed' | 'todo' | 'unknown';
type NoticeTone = keyof typeof NOTICE_CLASS;
/** One settled readiness/compatibility answer, from app.status or readmission. */
type Verdict = 'ready' | 'unreachable' | 'incompatibleHost' | 'incompatibleApp' | 'incompatibleUnknown' | 'unknown';
interface C6View {
  ready: Step;
  compatible: Step;
  result: MessageKey;
  /** Local selection only: the two settled answers that disagree. */
  disagree?: { service: Verdict; app: Verdict };
  tone: NoticeTone;
}

const VERDICT_KEY: Record<Verdict, MessageKey> = {
  ready: 'settings.local.c6.verdict.ready',
  unreachable: 'settings.local.c6.verdict.unreachable',
  incompatibleHost: 'settings.local.c6.verdict.incompatibleHost',
  incompatibleApp: 'settings.local.c6.verdict.incompatibleApp',
  incompatibleUnknown: 'settings.local.c6.verdict.incompatibleUnknown',
  unknown: 'settings.local.c6.verdict.unknown',
};

function incompatibleVerdict(side: 'host' | 'app' | 'unknown' | undefined): Verdict {
  return side === 'host' ? 'incompatibleHost' : side === 'app' ? 'incompatibleApp' : 'incompatibleUnknown';
}

function readmissionVerdict(readmission: LocalReadmission | null): Verdict {
  if (!readmission) return 'unknown';
  return readmission.state === 'incompatible' ? incompatibleVerdict(readmission.incompatible.side) : readmission.state;
}

/** Settled local app.status verdict; null while native is still settling it. */
function appVerdict(app: AppState): Verdict | null {
  switch (app.state) {
    case 'ready':
      return 'ready';
    case 'unreachable':
      return 'unreachable';
    case 'incompatible':
      return incompatibleVerdict(app.incompatible?.side);
    default:
      return null;
  }
}

const VERDICT_VIEW: Record<Verdict, C6View> = {
  ready: { ready: 'done', compatible: 'done', result: 'settings.local.c6.result.ready', tone: 'ok' },
  incompatibleHost: { ready: 'done', compatible: 'failed', result: 'settings.local.c6.result.serviceOld', tone: 'attention' },
  incompatibleApp: { ready: 'done', compatible: 'failed', result: 'settings.local.c6.result.appOld', tone: 'attention' },
  incompatibleUnknown: { ready: 'done', compatible: 'failed', result: 'settings.local.c6.result.compatUnknown', tone: 'attention' },
  unreachable: { ready: 'failed', compatible: 'todo', result: 'settings.local.c6.result.unreachable', tone: 'attention' },
  unknown: { ready: 'unknown', compatible: 'unknown', result: 'settings.local.c6.result.unconfirmed', tone: 'attention' },
};

/**
 * C6: service result, readiness and compatibility, each observed separately.
 * Local selection: app.status, with a named disagreement if the Start/Restart
 * readmission settled differently. Otherwise: this computer's readmission only.
 */
function c6View(app: AppState | null, readmission: LocalReadmission | null): C6View {
  if (app?.target?.kind !== 'local') return VERDICT_VIEW[readmissionVerdict(readmission)];
  const fromApp = appVerdict(app);
  if (!fromApp) return { ready: 'current', compatible: 'todo', result: 'settings.local.c6.result.checking', tone: 'progress' };
  const fromLocal = readmissionVerdict(readmission);
  if (readmission && fromLocal !== 'unknown' && fromLocal !== fromApp) {
    return { ready: 'unknown', compatible: 'unknown', result: 'settings.local.c6.result.disagree', disagree: { service: fromLocal, app: fromApp }, tone: 'attention' };
  }
  return VERDICT_VIEW[fromApp];
}

const STEP_CLASS: Record<Step, string> = {
  done: 'text-text-primary',
  current: 'text-text-primary font-semibold',
  failed: 'text-attention-text font-semibold',
  todo: 'text-text-muted',
  unknown: 'text-attention-text font-semibold',
};

const STEP_STATE_KEY: Record<Step, MessageKey> = {
  done: 'settings.local.c6.step.done',
  current: 'settings.local.c6.step.current',
  failed: 'settings.local.c6.step.failed',
  todo: 'settings.local.c6.step.todo',
  unknown: 'settings.local.c6.step.unknown',
};

function OutcomeNotice({ outcome, app, remoteHost, onRecheck }: { outcome: Outcome; app: AppState | null; remoteHost: string | null; onRecheck: () => void }): JSX.Element | null {
  const { t } = useTranslation();
  const action = t(ACTION_KEY[outcome.command]);
  if (outcome.kind === 'failed') {
    const named = REFUSAL_KEY[outcome.failure.code];
    const unknown = outcome.failure.effect !== 'none';
    const key: MessageKey = unknown
      ? outcome.command === 'local.restart'
        ? 'settings.local.refusal.restartUnknown'
        : 'settings.local.refusal.unknown'
      : named ?? 'settings.local.refusal.generic';
    return (
      <Notice tone={unknown ? 'danger' : 'attention'} testId="local-outcome">
        <p>{t(key, { action })}</p>
        {unknown && (
          <div className="mt-2">
            <Button size="sm" variant="outline" className={WRAP} onClick={onRecheck}>
              {t('settings.local.checkAgain')}
            </Button>
          </div>
        )}
      </Notice>
    );
  }
  if (outcome.command === 'local.stop') {
    return (
      <Notice tone="ok" testId="local-outcome">
        <p>{t('settings.local.done.stop')}</p>
      </Notice>
    );
  }
  if (outcome.command !== 'local.start' && outcome.command !== 'local.restart') return null;
  const steps = c6View(app, outcome.readmission);
  const params = steps.disagree ? { service: t(VERDICT_KEY[steps.disagree.service]), app: t(VERDICT_KEY[steps.disagree.app]) } : undefined;
  const rows: [MessageKey, Step][] = [
    [outcome.command === 'local.restart' ? 'settings.local.c6.step.restarted' : 'settings.local.c6.step.started', 'done'],
    ['settings.local.c6.step.ready', steps.ready],
    ['settings.local.c6.step.compatible', steps.compatible],
  ];
  return (
    <Notice tone={steps.tone} testId="local-outcome">
      <ol className="space-y-1" data-testid="local-c6-steps">
        {rows.map(([label, state]) => (
          <li key={label} data-step-state={state} className={STEP_CLASS[state]}>
            {t(label)} · <span className="font-normal">{t(STEP_STATE_KEY[state])}</span>
          </li>
        ))}
      </ol>
      <p className="mt-2" data-testid="local-c6-result">
        {t(steps.result, params)}
      </p>
      {remoteHost && (
        <p className="mt-1" data-testid="local-c6-remote">
          {t('settings.local.c6.remoteUnaffected', { host: remoteHost })}
          {steps.tone !== 'ok' && <> {t('settings.local.c6.remoteRecheck')}</>}
        </p>
      )}
      {steps.tone !== 'ok' && (
        <div className="mt-2">
          <Button size="sm" variant="outline" className={WRAP} onClick={onRecheck}>
            {t('settings.local.recheck')}
          </Button>
        </div>
      )}
    </Notice>
  );
}

function KeepRunningRow({
  value,
  known,
  unsupported,
  disabled,
  onSet,
  onRepair,
  onTurnOff,
}: {
  value: KeepRunning;
  known: boolean;
  unsupported: boolean;
  disabled: boolean;
  onSet: (enabled: boolean) => void;
  onRepair: () => void;
  onTurnOff: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  const labelId = useId();
  const hintId = useId();
  if (value === 'unavailable') {
    return (
      <Row testId="local-keep-row" title={t('settings.local.keep.title')} badge={null}>
        <p className="text-text-secondary text-sm">{t('settings.local.keep.macUnavailable')}</p>
      </Row>
    );
  }
  const interactive = known && !unsupported && (value === 'on' || value === 'off') && !disabled;
  const checked = value === 'on' ? true : value === 'partial' ? 'mixed' : false;
  const badge: { tone: Tone; key: MessageKey } = unsupported
    ? { tone: 'neutral', key: 'settings.local.keep.state.unavailable' }
    : value === 'on'
      ? { tone: 'positive', key: 'settings.local.keep.state.on' }
      : value === 'off'
        ? { tone: 'neutral', key: 'settings.local.keep.state.off' }
        : value === 'partial'
          ? { tone: 'attention', key: 'settings.local.keep.state.partial' }
          : value === 'checking'
            ? { tone: 'neutral', key: 'settings.local.state.checking' }
            : { tone: 'attention', key: 'settings.local.state.unknown' };
  const hint: MessageKey = unsupported
    ? 'settings.local.keep.hint.unavailable'
    : value === 'on'
      ? 'settings.local.keep.hint.on'
      : value === 'off'
        ? 'settings.local.keep.hint.off'
        : value === 'partial'
          ? 'settings.local.keep.hint.partial'
          : value === 'checking'
            ? 'settings.local.keep.hint.checking'
            : 'settings.local.keep.hint.unknown';
  return (
    <Row testId="local-keep-row" title={t('settings.local.keep.title')} badge={<StatusBadge tone={badge.tone} label={t(badge.key)} />}>
      <div className="flex min-w-0 items-start gap-3">
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-labelledby={labelId}
          aria-describedby={hintId}
          disabled={!interactive}
          onClick={() => onSet(value !== 'on')}
          data-testid="local-keep-switch"
          className={`focus-visible:ring-accent-default mt-0.5 inline-flex h-6 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:opacity-50 ${
            value === 'on' ? 'bg-accent-default' : value === 'partial' ? 'bg-feedback-warning/60' : 'bg-surface-sunken border-border-default border'
          }`}
        >
          <span
            className={`shadow-pasture-sm inline-block h-3.5 w-3.5 rounded-full transition-transform ${
              value === 'on' ? 'bg-primary-foreground translate-x-4' : value === 'partial' ? 'bg-text-muted translate-x-2.5' : 'bg-text-muted translate-x-0.5'
            }`}
          />
        </button>
        <div className="min-w-0 space-y-1">
          <p id={labelId} className="text-text-primary text-sm">
            {t('settings.local.keep.label')}
          </p>
          <p id={hintId} className="text-text-secondary text-xs">
            {t(hint)}
          </p>
        </div>
      </div>
      {value === 'partial' && !unsupported && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" className={WRAP} disabled={disabled} onClick={onRepair}>
            {t('settings.local.action.repair')}
          </Button>
          <Button size="sm" variant="secondary" className={WRAP} disabled={disabled} onClick={onTurnOff}>
            {t('settings.local.action.turnOff')}
          </Button>
        </div>
      )}
    </Row>
  );
}

function UpdateBadge({ update }: { update: UpdateStatus | null }): JSX.Element {
  switch (update) {
    case 'restart_required':
      return <Badge tone="attention" labelKey="settings.local.update.state.available" dot />;
    case 'not_detected':
      return <Badge tone="neutral" labelKey="settings.local.update.state.none" />;
    case 'unsupported':
      return <Badge tone="neutral" labelKey="settings.local.keep.state.unavailable" />;
    case 'unknown':
      return <Badge tone="attention" labelKey="settings.local.state.unknown" />;
    default:
      return <Badge tone="neutral" labelKey="settings.local.state.checking" dot />;
  }
}

function updateHintKey(update: UpdateStatus | null): MessageKey {
  switch (update) {
    case 'restart_required':
      return 'settings.local.update.hint.available';
    case 'not_detected':
      return 'settings.local.update.hint.none';
    case 'unsupported':
      return 'settings.local.update.hint.unsupported';
    case 'unknown':
      return 'settings.local.update.hint.unknown';
    default:
      return 'settings.local.update.hint.checking';
  }
}
