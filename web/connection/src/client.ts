import type { Connection } from './bridge';
import { catalogs } from './strings';
import type { Copy, Locale } from './strings';
import type { ActionResult, ClientSnapshot, Health } from './types';

// Client lifecycle presentation over closed native projections only. Every
// state shown here comes from a native snapshot/result; the web never infers
// Connected, never edits lists optimistically and never renders secrets.
type Key = keyof Copy;
type Stage = ClientSnapshot['stage'];
type Tone = 'neutral' | 'progress' | 'attention' | 'danger' | 'ok';
type Command = 'client.acceptInvite' | 'client.connectSaved' | 'client.removeSaved' | 'client.disconnect' |
  'client.cancelAttempt' | 'client.retry' | 'client.retrySave' | 'client.health' | 'client.status';
interface Failure { code: string; effect: string; recovery: string }
interface Outcome { command: Command; args: string; label: string | null; result?: ActionResult; error?: Failure }
interface Context { connection: () => Connection | null; locale: () => Locale; current: () => boolean }

const attempt: Stage[] = ['validating', 'joining', 'pairing', 'waitingApproval', 'saving', 'opening'];
const steps: Key[] = ['stepJoin', 'stepRequest', 'stepWait', 'stepSecure', 'stepOpen'];
const stepOf: Partial<Record<Stage, number>> = { joining: 0, pairing: 1, waitingApproval: 2, saving: 3, opening: 4 };
const badge: Record<Stage, [Key, Tone]> = {
  idle: ['badgeNotConnected', 'neutral'], validating: ['badgeChecking', 'progress'],
  joining: ['badgeConnecting', 'progress'], pairing: ['badgeConnecting', 'progress'],
  waitingApproval: ['badgeWaiting', 'attention'], saving: ['badgeSecuring', 'progress'],
  opening: ['badgeOpening', 'progress'], connected: ['badgeConnected', 'ok'],
  stopping: ['badgeStopping', 'progress'], failed: ['badgeNotConnected', 'danger'],
};
const actionName: Record<Command, Key> = {
  'client.acceptInvite': 'acceptInvite', 'client.connectSaved': 'connectSaved',
  'client.removeSaved': 'removeSaved', 'client.disconnect': 'disconnect',
  'client.cancelAttempt': 'cancelAttempt', 'client.retry': 'retryAttempt',
  'client.retrySave': 'retrySave', 'client.health': 'checkHealth', 'client.status': 'checkAgain',
};
const errorKey: Record<string, Key> = {
  busy: 'errBusy', permissionDenied: 'errPermissionDenied', stateChanged: 'errStateChanged',
  documentChanged: 'errStateChanged', expired: 'errExpired', unavailable: 'errUnavailable',
  outcomeUnknown: 'errOutcomeUnknown', invalidInvite: 'errInvalidInvite', newInviteRequired: 'errNewInvite',
  networkUnavailable: 'errNetwork', trustMismatch: 'errTrust', identityChanged: 'errIdentity',
  runtimeUnavailable: 'errRuntime', accessDenied: 'errAccessDenied', revokedConfirmed: 'errRevoked',
  keychainLocked: 'errLocked', keychainDenied: 'errStorageDenied', saveFailed: 'errSaveFailed',
  removalIncomplete: 'errRemovalIncomplete',
};

function parseFailure(error: unknown): Failure {
  try {
    const value = JSON.parse(String(error)) as Failure;
    if (typeof value.code === 'string' && typeof value.effect === 'string' && typeof value.recovery === 'string') return value;
  } catch { /* Closed fallback below; never reflect raw text. */ }
  return { code: 'outcomeUnknown', effect: 'unknown', recovery: 'refresh' };
}

export class ClientView {
  readonly root = document.createElement('section');
  private readonly live = document.createElement('p');
  private snapshot: ClientSnapshot | null = null;
  private health: Health | null = null;
  private healthFor = '';
  private outcome: Outcome | null = null;
  private pending: Command | null = null;
  private announced = '';
  private lastStage: Stage | null = null;
  private serial = 0;
  private gated = false;

  constructor(private readonly context: Context) {
    this.root.className = 'card client';
    this.root.setAttribute('aria-labelledby', 'client-title');
    this.live.className = 'visually-hidden';
    this.live.setAttribute('role', 'status');
    this.live.setAttribute('aria-live', 'polite');
  }
  get region(): HTMLElement { return this.live; }

  private copy(): Copy { return catalogs[this.context.locale()]; }
  private t(key: Key, values: Record<string, string> = {}): string {
    return this.copy()[key].replace(/\{(\w+)\}/g, (_, name: string) => values[name] ?? '');
  }
  private has(command: Command): boolean { return this.context.connection()?.has(command) ?? false; }
  private limited(field: string): boolean {
    return this.context.connection()?.hello.observationLimits.client.includes(field) ?? true;
  }
  private label(ref: string | null): string | null {
    const computer = this.snapshot?.computers?.find((item) => item.computerRef === ref);
    if (!computer) return null;
    const generated = this.context.connection()?.hello.platform === 'linux'
      ? /^Saved computer ([1-9][0-9]*)$/.exec(computer.label) : null;
    const ordinal = generated ? Number(generated[1]) : NaN;
    return Number.isSafeInteger(ordinal)
      ? `${this.copy().savedComputer} ${new Intl.NumberFormat(this.context.locale()).format(ordinal)}`
      : computer.label;
  }

  update(snapshot: ClientSnapshot): void {
    const previous = this.snapshot?.stage;
    this.snapshot = snapshot;
    if (snapshot.stage !== 'connected') { this.health = null; this.healthFor = ''; }
    // Connected only from native; fetch health once per connected entry.
    if (snapshot.stage === 'connected' && previous !== 'connected' && this.has('client.health')) void this.readHealth();
    this.render();
  }

  private async readHealth(): Promise<void> {
    const connection = this.context.connection();
    if (!connection) return;
    const marker = `${Date.now()}`;
    this.healthFor = marker;
    try {
      const health = JSON.parse(await connection.call('client.health', '{}')) as Health;
      if (this.context.current() && this.healthFor === marker && this.snapshot?.stage === 'connected') this.health = health;
    } catch (error) {
      if (this.context.current()) this.outcome = { command: 'client.health', args: '{}', label: null, error: parseFailure(error) };
    }
    if (this.context.current()) this.render();
  }

  private async refresh(): Promise<void> {
    const connection = this.context.connection();
    if (!connection) return;
    try {
      if (connection.has('client.savedComputers')) await connection.call('client.savedComputers', '{}');
      const raw = await connection.call('client.status', '{}');
      if (this.context.current()) this.update(JSON.parse(raw) as ClientSnapshot);
    } catch (error) {
      if (this.context.current()) { this.outcome = { command: 'client.status', args: '{}', label: null, error: parseFailure(error) }; this.render(); }
    }
  }

  private async run(command: Command, args = '{}', label: string | null = null): Promise<void> {
    const connection = this.context.connection();
    if (!connection) return;
    if (command === 'client.health') { this.outcome = null; await this.readHealth(); return; }
    if (command === 'client.status') { this.outcome = null; this.render(); await this.refresh(); return; }
    const cancellation = command === 'client.cancelAttempt';
    if (!cancellation) this.pending = command;
    this.outcome = null;
    this.render();
    let outcome: Outcome;
    try {
      outcome = { command, args, label, result: JSON.parse(await connection.call(command, args)) as ActionResult };
    } catch (error) { outcome = { command, args, label, error: parseFailure(error) }; }
    if (!cancellation) this.pending = null;
    if (!this.context.current()) return;
    this.outcome = outcome;
    this.serial++;
    // Reread native state after every mutation; never edit lists optimistically.
    await this.refresh();
    if (this.context.current()) this.render();
  }

  private button(command: Command, key: Key, options: { primary?: boolean; danger?: boolean; args?: string; label?: string | null; id?: string } = {}): HTMLButtonElement {
    const node = document.createElement('button');
    node.type = 'button';
    node.textContent = this.t(key);
    node.dataset.focusKey = options.id ?? `${command}:${options.args ?? ''}:${key}`;
    if (options.primary) node.className = 'primary';
    if (options.danger) node.className = 'danger';
    const mutation = !['client.health', 'client.status', 'client.cancelAttempt'].includes(command);
    // aria-disabled (not disabled) keeps keyboard focus on the control while busy.
    if (mutation && this.pending !== null) node.setAttribute('aria-disabled', 'true');
    if (this.pending === command) node.setAttribute('aria-busy', 'true');
    node.addEventListener('click', () => {
      if (mutation && this.pending !== null) return;
      void this.run(command, options.args, options.label ?? null);
    });
    return node;
  }

  private text(tag: 'p' | 'h2' | 'span' | 'strong' | 'dt' | 'dd', content: string, className?: string): HTMLElement {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  }

  private notice(content: string, tone: Tone, className = 'notice'): HTMLElement {
    if (className === 'hint') return this.text('p', content, 'hint');
    return this.text('p', content, className === 'notice' ? `notice tone-${tone}` : `notice ${className} tone-${tone}`);
  }

  private actions(items: (HTMLButtonElement | null)[], gated: boolean): HTMLElement[] {
    const row = document.createElement('div');
    row.className = 'actions';
    items.forEach((item) => { if (item) row.append(item); });
    this.gated ||= gated;
    return row.childElementCount ? [row] : [];
  }

  private gate(command: Command, make: () => HTMLButtonElement): { node: HTMLButtonElement | null; missing: boolean } {
    return this.has(command) ? { node: make(), missing: false } : { node: null, missing: true };
  }

  private steps(stage: Stage): HTMLElement {
    const list = document.createElement('ol');
    list.className = 'steps';
    const current = stepOf[stage] ?? -1;
    steps.forEach((key, index) => {
      const item = document.createElement('li');
      const state = index < current ? 'done' : index === current ? 'current' : 'todo';
      item.className = state;
      item.append(this.text('span', this.t(key)));
      if (state !== 'todo') item.append(this.text('span', ` · ${this.t(state === 'done' ? 'stepDone' : 'stepCurrent')}`, 'step-state'));
      if (state === 'current') item.setAttribute('aria-current', 'step');
      list.append(item);
    });
    return list;
  }

  private savedList(stage: Stage): HTMLElement[] {
    const snapshot = this.snapshot;
    if (!snapshot) return [];
    const nodes: HTMLElement[] = [this.text('h2', this.t('savedTitle'), 'section-title')];
    const unreadable = snapshot.computers === null || this.limited('computers') || snapshot.savedAccess === 'unknown';
    if (snapshot.computers && snapshot.computers.length > 0) {
      const list = document.createElement('ul');
      list.className = 'computers';
      let missing = false;
      for (const computer of snapshot.computers) {
        const name = this.label(computer.computerRef) ?? computer.label;
        const args = JSON.stringify({ computerRef: computer.computerRef });
        const row = document.createElement('li');
        const info = document.createElement('div');
        info.append(this.text('strong', name));
        if (computer.computerRef === snapshot.activeComputerRef && stage !== 'idle' && stage !== 'failed') info.append(this.text('span', ` · ${this.t('currentComputer')}`, 'muted'));
        const buttons = document.createElement('div');
        buttons.className = 'row-actions';
        const idle = stage === 'idle' || stage === 'failed';
        if (idle) {
          const connect = this.gate('client.connectSaved', () => this.button('client.connectSaved', 'connectSaved', { args, label: name }));
          const remove = this.gate('client.removeSaved', () => this.button('client.removeSaved', 'removeSaved', { args, label: name, danger: true }));
          missing ||= connect.missing || remove.missing;
          [connect.node, remove.node].forEach((node) => {
            if (node) { node.setAttribute('aria-label', `${node.textContent ?? ''}: ${name}`); buttons.append(node); }
          });
        }
        row.append(info, buttons);
        list.append(row);
      }
      nodes.push(list);
      this.gated ||= missing;
      nodes.push(this.text('p', this.t('savedLabelsHint'), 'hint'));
    } else if (unreadable) {
      nodes.push(this.notice(this.t('savedUnknownAll'), 'attention'));
    } else {
      nodes.push(this.text('p', this.t('savedEmpty')));
    }
    if (snapshot.computers && snapshot.computers.length > 0 && snapshot.savedAccess === 'unknown') nodes.push(this.notice(this.t('savedUnknown'), 'attention'));
    if (snapshot.savedAccess === 'needsPermission') nodes.push(this.notice(this.t('savedLocked'), 'attention'));
    if (snapshot.savedAccess === 'removalIncomplete') nodes.push(this.notice(this.t('errRemovalIncomplete'), 'attention'));
    return nodes;
  }

  private healthFacts(): HTMLElement {
    const health = this.health;
    const list = document.createElement('dl');
    list.className = 'facts';
    const known = health !== null && health.observation !== 'unavailable';
    const value = (field: 'access' | 'runtime' | 'route', raw: string | undefined): string => {
      if (!known || raw === undefined || raw === 'unknown' || this.limited(field)) return this.t('unknownValue');
      const keys: Record<string, Key> = {
        allowed: 'accessAllowed', accessDenied: 'accessDenied', revokedConfirmed: 'accessRevoked',
        available: 'runtimeAvailable', offline: 'runtimeOffline', restarting: 'runtimeRestarting',
        direct: 'routeDirect', relay: 'routeRelay',
      };
      return keys[raw] ? this.t(keys[raw]) : this.t('unknownValue');
    };
    const time = health?.observedAt && known && !this.limited('observedAt')
      ? new Intl.DateTimeFormat(this.context.locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(health.observedAt))
      : this.t('unknownValue');
    for (const [label, content] of [
      [this.t('healthAccess'), value('access', health?.access)],
      [this.t('healthRuntime'), value('runtime', health?.runtime)],
      [this.t('healthRoute'), value('route', health?.route)],
      [this.t('healthChecked'), time],
    ] as const) {
      const row = document.createElement('div');
      row.append(this.text('dt', label), this.text('dd', content));
      list.append(row);
    }
    return list;
  }

  private outcomeNodes(): HTMLElement[] {
    const outcome = this.outcome;
    if (!outcome) return [];
    const nodes: HTMLElement[] = [];
    const name = outcome.label ?? '';
    const action = this.t(actionName[outcome.command]);
    if (outcome.result) {
      const { effect, localCleanup } = outcome.result;
      if (effect === 'unknown') nodes.push(this.notice(this.t('resultUnknown', { action }), 'attention'));
      else if (effect === 'confirmed') {
        const key: Partial<Record<Command, Key>> = {
          'client.cancelAttempt': 'cancelledConfirmed', 'client.disconnect': this.snapshot?.savedAccess === 'saved' ? 'disconnectedSaved' : 'disconnectedUnsaved',
          'client.removeSaved': localCleanup === 'incomplete' || localCleanup === 'unknown' ? 'errRemovalIncomplete' : 'removedConfirmed',
          'client.retrySave': 'savedConfirmed',
        };
        const message = key[outcome.command];
        if (message) nodes.push(this.notice(this.t(message, { name }), message === 'errRemovalIncomplete' ? 'attention' : 'ok'));
      } else if (['client.cancelAttempt', 'client.disconnect', 'client.removeSaved'].includes(outcome.command)) {
        nodes.push(this.notice(this.t('nothingChanged', { action }), 'neutral'));
      }
      if (effect === 'unknown' && this.has('client.status')) nodes.push(...this.actions([this.button('client.status', 'checkAgain')], false));
      return nodes;
    }
    const error = outcome.error;
    if (!error) return nodes;
    const message = errorKey[error.code] ?? 'errUnavailable';
    const text = `${this.t('failedAction', { action })} ${this.t(message, { name })}` +
      (error.effect === 'unknown' && error.code !== 'outcomeUnknown' ? ` ${this.t('effectUnknown')}` : '');
    nodes.push(this.notice(text, error.effect === 'unknown' ? 'attention' : 'danger'));
    const recovery: (HTMLButtonElement | null)[] = [];
    if (error.recovery === 'refresh') recovery.push(this.button('client.status', 'checkAgain'));
    // Recovery keeps the originating action and its exact record.
    if (error.recovery === 'explicitRetry' || error.recovery === 'nativePermission') {
      const stillListed = outcome.command !== 'client.connectSaved' && outcome.command !== 'client.removeSaved' ||
        this.snapshot?.computers?.some((item) => JSON.stringify({ computerRef: item.computerRef }) === outcome.args);
      // The persistent save warning already offers Retry save while it applies.
      const covered = outcome.command === 'client.retrySave' && this.snapshot?.stage === 'connected';
      if (stillListed && this.has(outcome.command) && !covered) {
        const again = this.button(outcome.command, 'tryAgainAction', { args: outcome.args, label: outcome.label, id: `retry:${outcome.command}:${outcome.args}` });
        again.textContent = this.t('tryAgainAction', { action });
        if (name) again.setAttribute('aria-label', `${again.textContent}: ${name}`);
        recovery.push(again);
      } else if (!stillListed) nodes.push(this.notice(this.t('recordGone', { name }), 'neutral', 'hint'));
    }
    if (error.recovery === 'newInviteOnHost') {
      // The state's own Paste invite action follows; no duplicate control here.
      nodes.push(this.notice(this.t('newInviteHint'), 'neutral', 'hint'));
    }
    if (error.recovery === 'nativeRecovery' || error.recovery === 'operatorRecovery') nodes.push(this.notice(this.t('nativeRecoveryHint'), 'neutral', 'hint'));
    nodes.push(...this.actions(recovery, false));
    return nodes;
  }

  private body(stage: Stage, name: string | null): { title: string; nodes: HTMLElement[] } {
    const snapshot = this.snapshot!;
    const nodes: HTMLElement[] = [];
    const inFlight = attempt.includes(stage);
    if (inFlight && name) {
      const destination = document.createElement('div');
      destination.className = 'destination';
      destination.append(this.text('span', this.t('destination'), 'muted'), this.text('strong', name));
      nodes.push(destination);
    }
    const titles: Record<Stage, Key> = {
      idle: snapshot.computers?.length ? 'titleIdleSaved' : 'titleIdle', validating: 'titleValidating',
      joining: 'titleJoining', pairing: 'titlePairing', waitingApproval: 'titleWaiting', saving: 'titleSaving',
      opening: 'titleOpening', connected: name ? 'titleConnectedTo' : 'titleConnected', stopping: 'titleStopping', failed: 'titleFailed',
    };
    const title = this.t(titles[stage], { name: name ?? '' });
    const lead: Partial<Record<Stage, Key>> = {
      idle: 'leadIdle', validating: 'leadValidating', waitingApproval: 'leadWaiting', saving: 'leadSaving',
      connected: 'leadConnected', stopping: 'leadStopping', failed: 'leadFailed',
    };
    const leadKey = stage === 'idle' && !this.has('client.acceptInvite') ? undefined : lead[stage];
    if (leadKey) nodes.push(this.text('p', this.t(leadKey)));
    nodes.push(...this.outcomeNodes());
    if (inFlight && stage !== 'validating') nodes.push(this.steps(stage));
    if (inFlight && snapshot.expiresAt && ['validating', 'joining', 'pairing', 'waitingApproval'].includes(stage) && !this.limited('expiresAt')) {
      const time = new Intl.DateTimeFormat(this.context.locale(), { hour: '2-digit', minute: '2-digit' }).format(new Date(snapshot.expiresAt));
      nodes.push(this.notice(this.t('inviteExpiresAt', { time }), 'attention', 'deadline'));
    }
    if (inFlight || stage === 'idle' || stage === 'failed') nodes.push(this.notice(this.t('nativeDialogs'), 'neutral', 'native-note'));
    if (stage === 'connected') {
      if (['notSaved', 'needsPermission', 'unknown'].includes(snapshot.savedAccess) && !this.limited('savedAccess')) {
        const key: Key = snapshot.savedAccess === 'needsPermission' ? 'saveLocked' : snapshot.savedAccess === 'unknown' ? 'saveUnknown' : 'saveFailedWarning';
        const warning = document.createElement('div');
        warning.className = 'warning tone-attention';
        warning.append(this.text('strong', this.t('saveWarningTitle')), this.text('p', this.t(key)));
        const retry = this.gate('client.retrySave', () => this.button('client.retrySave', 'retrySave', { primary: true }));
        warning.append(...this.actions([retry.node], retry.missing));
        if (retry.node) warning.append(this.text('p', this.t('systemAsks'), 'hint'));
        nodes.push(warning);
      }
      nodes.push(this.text('h2', this.t('healthTitle'), 'section-title'), this.healthFacts());
      if (this.health?.observation === 'stale') nodes.push(this.notice(this.t('healthStale'), 'attention', 'hint'));
      if (this.health?.access === 'accessDenied') nodes.push(this.notice(this.t('errAccessDenied'), 'danger'));
      if (this.health?.access === 'revokedConfirmed') nodes.push(this.notice(this.t('errRevoked'), 'danger'));
    }
    if (stage === 'idle' && snapshot.savedAccess === 'notSaved') nodes.push(this.notice(this.t('lastNotSaved'), 'attention'));
    const buttons: { node: HTMLButtonElement | null; missing: boolean }[] = [];
    if (inFlight) buttons.push(this.gate('client.cancelAttempt', () => this.button('client.cancelAttempt', 'cancelAttempt')));
    if (stage === 'failed') buttons.push(this.gate('client.retry', () => this.button('client.retry', 'retryAttempt', { primary: true })));
    if (stage === 'idle' || stage === 'failed') buttons.push(this.gate('client.acceptInvite', () => this.button('client.acceptInvite', stage === 'failed' ? 'acceptNewInvite' : 'acceptInvite', { primary: stage === 'idle' })));
    if (stage === 'connected') {
      buttons.push(this.gate('client.health', () => this.button('client.health', 'checkHealth')));
      buttons.push(this.gate('client.disconnect', () => this.button('client.disconnect', 'disconnect')));
    }
    nodes.push(...this.actions(buttons.map((item) => item.node), buttons.some((item) => item.missing)));
    if (inFlight && this.has('client.cancelAttempt')) nodes.push(this.text('p', this.t(stage === 'saving' ? 'cancelSafePoint' : 'cancelLocalOnly'), 'hint'));
    if (stage === 'idle' || stage === 'failed' || stage === 'connected') nodes.push(...this.savedList(stage));
    // One honest update note when any action relevant to this state is absent.
    if (this.gated) nodes.push(this.notice(this.t('capabilityMissing'), 'neutral', 'hint'));
    return { title, nodes };
  }

  render(): void {
    if (!this.snapshot) return;
    const stage = this.snapshot.stage;
    const name = this.label(this.snapshot.activeComputerRef);
    const focused = document.activeElement instanceof HTMLElement && this.root.contains(document.activeElement)
      ? document.activeElement.dataset.focusKey ?? (document.activeElement.id === 'client-title' ? 'title' : null) : null;
    const [badgeKey, tone] = badge[stage];
    const header = document.createElement('div');
    header.className = 'card-header';
    header.append(this.text('span', this.t('clientEyebrow'), 'eyebrow'), this.text('span', this.t(badgeKey), `badge tone-${tone}`));
    this.gated = false;
    const { title, nodes } = this.body(stage, name);
    const heading = this.text('h2', title, 'card-title');
    heading.id = 'client-title';
    heading.tabIndex = -1;
    const content = document.createElement('div');
    content.className = 'card-body';
    const lead = nodes[0]?.classList.contains('destination') ? [nodes.shift()!] : [];
    content.append(...lead, heading, ...nodes);
    this.root.setAttribute('aria-busy', String(this.pending !== null));
    this.root.replaceChildren(header, content);
    // Announce only stage/outcome changes, not every snapshot or language switch.
    const outcomeText = nodes.filter((node) => node.classList.contains('notice') && node.classList.contains('tone-ok') ||
      node.classList.contains('tone-danger')).map((node) => node.textContent).join(' ');
    const announcement = `${this.t(badgeKey)}. ${title}${outcomeText ? `. ${outcomeText}` : ''}`;
    // Language-independent key: switching language never re-announces.
    const key = `${stage}|${name ?? ''}|${outcomeText ? this.serial : ''}`;
    if (key !== this.announced) { this.announced = key; this.live.textContent = announcement; }
    if (focused) {
      const target = focused === 'title' ? heading : this.root.querySelector<HTMLElement>(`[data-focus-key="${CSS.escape(focused)}"]`);
      (target ?? heading).focus();
    } else if (this.lastStage !== null && this.lastStage !== stage && document.activeElement === document.body) heading.focus();
    this.lastStage = stage;
  }

  /** Language change: same state, new copy, focus preserved, no native calls. */
  translate(): void { this.render(); }
}
