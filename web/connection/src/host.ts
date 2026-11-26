import type { Connection } from './bridge';
import { parseFailure } from './client';
import type { Failure } from './client';
import { catalogs } from './strings';
import type { Copy, Locale } from './strings';
import type { ActionResult, Cleanup, HostSnapshot } from './types';

// Linux HOST lifecycle presentation over the closed v1 host commands/events only.
// Native owns every effect, prompt and record: the web never renders invite
// bytes, fingerprints or comparison phrases, never supplies a device identity
// and never edits the device list optimistically.
type Key = keyof Copy;
type Tone = 'neutral' | 'progress' | 'attention' | 'danger' | 'ok';
type Phase = HostSnapshot['setup'];
const stateCopy: Partial<Record<Phase, Key>> = {
  prepared_no_receipt: 'hStatePrepared', failed_uncertain: 'hStateFailedUncertain',
  partial_cleanup: 'hStatePartial', runtime_unavailable: 'hStateRuntime',
  setup_unverified: 'hStateUnverified', renewal_failed: 'hStateRenewal',
};
const canTurnOff = (phase: Phase): boolean =>
  ['ready', 'paused', 'failed_uncertain', 'partial_cleanup', 'runtime_unavailable', 'setup_unverified', 'renewal_failed'].includes(phase);
type Command = 'host.enable' | 'host.turnOff' | 'host.createInvite' | 'host.approveDevice' |
  'host.rejectDevice' | 'host.nextDevice' | 'host.revokeDevice' | 'host.status';
type Confirmable = 'host.turnOff' | 'host.revokeDevice';
// raw is the native device label captured when the action was requested.
interface Outcome { command: Command; raw: string | null; result?: ActionResult; error?: Failure; read?: Failure }
// observe is the shell's single observation owner: it updates the runtime line
// and then calls update(), so card and shell never disagree.
interface Context { connection: () => Connection | null; locale: () => Locale; current: () => boolean; observe: (snapshot: HostSnapshot) => void }

const actionName: Record<Command, Key> = {
  'host.enable': 'hTurnOn', 'host.turnOff': 'hTurnOff', 'host.createInvite': 'hAddDevice',
  'host.approveDevice': 'hApprove', 'host.rejectDevice': 'hReject', 'host.nextDevice': 'hNextDevice',
  'host.revokeDevice': 'hRevoke', 'host.status': 'checkAgain',
};
const errorKey: Record<string, Key> = {
  busy: 'errBusy', permissionDenied: 'errPermissionDenied', stateChanged: 'hErrStateChanged',
  documentChanged: 'hErrStateChanged', expired: 'hErrExpired', unavailable: 'errUnavailable',
  outcomeUnknown: 'errOutcomeUnknown', runtimeUnavailable: 'hErrRuntime',
  hostStateUnrecognized: 'hErrStateUnrecognized',
};
const cleanupKey: Record<Cleanup, Key | null> = {
  confirmed: 'hConfirmed', incomplete: 'hIncomplete', unknown: 'unknownValue', notApplicable: null,
};
// Commands whose recovery must still target the same native record.
const recordBound: Command[] = ['host.approveDevice', 'host.rejectDevice', 'host.revokeDevice'];

export class HostView {
  readonly root = document.createElement('section');
  private readonly live = document.createElement('p');
  private snapshot: HostSnapshot | null = null;
  private outcome: Outcome | null = null;
  private pending: Command | null = null;
  private confirm: { command: Confirmable; raw: string | null; opener: string } | null = null;
  private focusNext: string | null = null;
  private announced = '';
  private lastPhase: string | null = null;
  private serial = 0;
  private gated = false;
  // Same fencing as the client view: every native observation, read start,
  // completed mutation and role (re)load bumps the revision; the newest wins.
  private revision = 0;
  private selection = 0;
  // Set when an action's effect is uncertain and the reread failed: the retained
  // snapshot no longer proves the current state. Only a later observation clears it.
  private unconfirmed = false;

  constructor(private readonly context: Context) {
    this.root.className = 'card host';
    this.root.setAttribute('aria-labelledby', 'host-title');
    this.live.className = 'visually-hidden';
    this.live.setAttribute('role', 'status');
    this.live.setAttribute('aria-live', 'polite');
  }
  get region(): HTMLElement { return this.live; }

  fence(): number { return ++this.revision; }
  latest(token: number): boolean { return token === this.revision; }
  reset(): void { this.selection++; this.revision++; this.confirm = null; this.outcome = null; this.unconfirmed = false; }

  private copy(): Copy { return catalogs[this.context.locale()]; }
  private t(key: Key, values: Record<string, string> = {}): string {
    return this.copy()[key].replace(/\{(\w+)\}/g, (_, name: string) => values[name] ?? '');
  }
  private has(command: Command): boolean { return this.context.connection()?.has(command) ?? false; }
  private limited(field: string): boolean {
    return this.context.connection()?.hello.observationLimits.host.includes(field) ?? true;
  }
  private phase(): Phase {
    const snapshot = this.snapshot;
    if (!snapshot || snapshot.observation === 'unavailable' || this.limited('observation') || this.limited('setup')) return 'unknown';
    return snapshot.setup;
  }
  /** Native label of the current pending request / helper-selected device, when observable. */
  private pendingLabel(): string | null { return this.limited('pending') ? null : this.snapshot?.pending?.label ?? null; }
  private selectedLabel(): string | null { return this.limited('selected') ? null : this.snapshot?.selected?.label ?? null; }

  update(snapshot: HostSnapshot): void {
    this.revision++;
    this.snapshot = snapshot;
    this.unconfirmed = false;
    // A confirmation is bound to the record/state it was opened for.
    const phase = this.phase();
    if (this.confirm?.command === 'host.revokeDevice' && (this.selectedLabel() !== this.confirm.raw || phase !== 'ready' && phase !== 'paused')) this.confirm = null;
    if (this.confirm?.command === 'host.turnOff' && !canTurnOff(phase)) this.confirm = null;
    this.render();
  }

  private setOutcome(outcome: Outcome | null): void { this.outcome = outcome; if (outcome) this.serial++; }

  /** Rereads host.status; a read failure attaches to the originating outcome. */
  private async refresh(origin: Outcome | null): Promise<void> {
    const connection = this.context.connection();
    if (!connection) return;
    const selection = this.selection;
    const token = this.fence();
    const live = (): boolean => this.context.current() && this.selection === selection && this.latest(token);
    let failure: Failure | null = null;
    try {
      const raw = await connection.call('host.status', '{}');
      if (!live()) return;
      if (origin) origin.read = undefined;
      this.context.observe(JSON.parse(raw) as HostSnapshot);
    } catch (error) {
      if (!live()) return;
      failure = parseFailure(error);
    }
    if (failure) {
      if (origin && this.outcome === origin) {
        origin.read = failure; this.serial++;
        if (origin.result?.effect === 'unknown' || origin.error?.effect === 'unknown') this.unconfirmed = true;
      } else if (!origin) this.setOutcome({ command: 'host.status', raw: null, error: failure });
    }
    this.render();
  }

  private async run(command: Command, raw: string | null = null): Promise<void> {
    const connection = this.context.connection();
    if (!connection) return;
    this.confirm = null;
    if (command === 'host.status') {
      // While unconfirmed, Check again keeps the uncertain outcome it is resolving.
      const origin = this.unconfirmed ? this.outcome : null;
      if (!origin) this.outcome = null;
      this.render(); await this.refresh(origin); return;
    }
    const selection = this.selection;
    // Observation revision at dispatch: a NextDevice reply overtaken by a newer
    // observation or read is discarded and is not counted as an observation.
    const before = this.revision;
    this.pending = command;
    this.outcome = null;
    this.render();
    let outcome: Outcome;
    let selected: HostSnapshot | null = null;
    try {
      const reply = await connection.call(command, '{}');
      if (command === 'host.nextDevice') selected = JSON.parse(reply) as HostSnapshot;
      outcome = { command, raw, result: command === 'host.nextDevice' ? undefined : JSON.parse(reply) as ActionResult };
    } catch (error) { outcome = { command, raw, error: parseFailure(error) }; }
    this.pending = null;
    if (!this.context.current() || this.selection !== selection) return;
    if (selected) {
      if (this.latest(before)) this.context.observe(selected); else this.render();
      return;
    }
    // A completed mutation supersedes every read started before it.
    this.fence();
    this.setOutcome(outcome);
    this.render();
    // Reread native state after every mutation; never edit lists optimistically.
    await this.refresh(outcome);
  }

  private button(command: Command, key: Key, options: { primary?: boolean; danger?: boolean; raw?: string | null; id?: string; label?: string } = {}): HTMLButtonElement {
    const node = document.createElement('button');
    node.type = 'button';
    node.textContent = options.label ?? this.t(key);
    node.dataset.focusKey = options.id ?? `${command}:${key}`;
    if (options.primary) node.className = 'primary';
    if (options.danger) node.className = 'danger';
    const mutation = command !== 'host.status';
    // aria-disabled (not disabled) keeps keyboard focus on the control while busy.
    if (mutation && this.pending !== null) node.setAttribute('aria-disabled', 'true');
    if (this.pending === command) node.setAttribute('aria-busy', 'true');
    node.addEventListener('click', () => {
      if (mutation && this.pending !== null) return;
      if (command === 'host.turnOff' || command === 'host.revokeDevice') {
        if (!options.id?.startsWith('confirm:') && !options.id?.startsWith('retry:')) {
          this.confirm = { command, raw: options.raw ?? null, opener: node.dataset.focusKey ?? '' };
          this.focusNext = 'confirm:cancel';
          this.render();
          return;
        }
      }
      void this.run(command, options.raw ?? null);
    });
    return node;
  }
  private text(tag: 'p' | 'h2' | 'span' | 'strong' | 'dt' | 'dd' | 'li', content: string, className?: string): HTMLElement {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  }
  private notice(content: string, tone: Tone, outcome = false): HTMLElement {
    const node = this.text('p', content, `notice tone-${tone}`);
    if (outcome) node.dataset.outcome = 'true';
    return node;
  }
  private actions(items: (HTMLButtonElement | null)[]): HTMLElement[] {
    const row = document.createElement('div');
    row.className = 'actions';
    items.forEach((item) => { if (item) row.append(item); });
    return row.childElementCount ? [row] : [];
  }
  /** Capability-gated control: an absent command hides it and records one update note. */
  private gate(command: Command, make: () => HTMLButtonElement): HTMLButtonElement | null {
    if (this.has(command)) return make();
    this.gated = true;
    return null;
  }
  private time(value: string): string {
    return new Intl.DateTimeFormat(this.context.locale(), { hour: '2-digit', minute: '2-digit' }).format(new Date(value));
  }

  private components(result: ActionResult): HTMLElement[] {
    const rows: [Key, Cleanup][] = [['hCompDenial', result.hostDenial], ['hCompHost', result.hostCleanup], ['hCompNetwork', result.networkCleanup]];
    const list = document.createElement('dl');
    list.className = 'facts';
    for (const [label, value] of rows) {
      const key = cleanupKey[value];
      if (!key) continue;
      const row = document.createElement('div');
      row.append(this.text('dt', this.t(label)), this.text('dd', this.t(key)));
      list.append(row);
    }
    return list.childElementCount ? [list] : [];
  }

  private outcomeNodes(): HTMLElement[] {
    const outcome = this.outcome;
    if (!outcome) return [];
    const nodes: HTMLElement[] = [];
    const reasonKey = (failure: Failure): Key => {
      const detail = failure.code === 'enablePreparationFailed' ? 'enableFailure.' + failure.category :
        failure.code === 'hostStateUnrecognized' ? 'hostState.' + (failure.category === 'unknown' ? 'unverified' : failure.category) : '';
      return Object.hasOwn(catalogs.en, detail) ? detail as Key : errorKey[failure.code] ?? 'errUnavailable';
    };
    const name = outcome.raw ?? '';
    const action = this.t(actionName[outcome.command]);
    const checkAgain = (): HTMLButtonElement | null => this.has('host.status') ? this.button('host.status', 'checkAgain', { id: 'outcome:check' }) : null;
    if (outcome.result) {
      const result = outcome.result;
      if (result.effect === 'unknown') {
        // Unknown effect is never “nothing changed” and never a full Off/revoked claim.
        nodes.push(this.notice(this.t('resultUnknown', { action }), 'attention', true));
      } else if (result.effect === 'confirmed') {
        const revokedConfirmed = result.hostDenial === 'confirmed';
        const complete = [result.hostDenial, result.hostCleanup, result.networkCleanup].every((value) => value === 'confirmed' || value === 'notApplicable');
        const messages: Partial<Record<Command, [Key, Tone]>> = {
          'host.enable': ['hEnableConfirmed', 'ok'], 'host.turnOff': ['hTurnOffConfirmed', complete ? 'ok' : 'attention'],
          'host.createInvite': [result.inviteState === 'issued' ? 'hInviteCreated' : 'hInviteUnconfirmed', result.inviteState === 'issued' ? 'ok' : 'attention'],
          'host.approveDevice': ['hApproved', 'ok'], 'host.rejectDevice': ['hRejected', 'ok'],
          'host.revokeDevice': [revokedConfirmed ? 'hRevoked' : 'hRevokeUnconfirmed', revokedConfirmed ? 'ok' : 'attention'],
        };
        const message = messages[outcome.command];
        if (message) nodes.push(this.notice(this.t(message[0], { name }), message[1], true));
      } else {
        nodes.push(this.notice(this.t('nothingChanged', { action }), 'neutral', true));
      }
      if (outcome.command === 'host.turnOff' || outcome.command === 'host.revokeDevice') nodes.push(...this.components(result));
      if (result.effect === 'unknown') nodes.push(...this.actions([checkAgain()]));
    } else if (outcome.error) {
      const error = outcome.error;
      const text = `${this.t('failedAction', { action })} ${this.t(reasonKey(error))}` +
        (error.effect === 'unknown' && error.code !== 'outcomeUnknown' ? ` ${this.t('effectUnknown')}` : '');
      nodes.push(this.notice(text, error.effect === 'unknown' ? 'attention' : 'danger', true));
      const recovery: (HTMLButtonElement | null)[] = [];
      if (error.recovery === 'refresh') recovery.push(checkAgain());
      if (error.recovery === 'explicitRetry' || error.recovery === 'nativePermission') {
        // Recovery retries the originating command, only while its native record still applies.
        const current = outcome.command === 'host.revokeDevice' ? this.selectedLabel() : this.pendingLabel();
        const still = !recordBound.includes(outcome.command) || current !== null && current === outcome.raw;
        if (still && this.has(outcome.command)) {
          const label = this.t('tryAgainAction', { action });
          const again = this.button(outcome.command, 'tryAgainAction', { raw: outcome.raw, id: `retry:${outcome.command}`, label });
          if (name) again.setAttribute('aria-label', `${label}: ${name}`);
          recovery.push(again);
        } else if (!still) nodes.push(this.text('p', this.t('hRecordGone', { name }), 'hint'));
      }
      if (error.recovery === 'newInviteOnHost') nodes.push(this.text('p', this.t('hNewInviteHint'), 'hint'));
      if (error.recovery === 'nativeRecovery' || error.recovery === 'operatorRecovery') nodes.push(this.text('p', this.t('nativeRecoveryHint'), 'hint'));
      nodes.push(...this.actions(recovery));
    }
    const read = outcome.read;
    if (read) {
      nodes.push(this.notice(`${this.t('failedAction', { action: this.t('checkAgain') })} ${this.t(reasonKey(read))}`,
        read.effect === 'unknown' ? 'attention' : 'danger', true));
      if (read.recovery === 'refresh' && !nodes.some((node) => node.querySelector('[data-focus-key="outcome:check"]'))) nodes.push(...this.actions([checkAgain()]));
    }
    return nodes;
  }

  private confirmation(): HTMLElement[] {
    const confirm = this.confirm;
    if (!confirm) return [];
    const box = document.createElement('div');
    box.className = 'warning tone-danger confirm';
    const name = confirm.raw ?? '';
    const turnOff = confirm.command === 'host.turnOff';
    box.append(this.text('strong', this.t(turnOff ? 'hConfirmTurnOffTitle' : 'hConfirmRevokeTitle', { name })),
      this.text('p', this.t(turnOff ? 'hConfirmTurnOff' : 'hConfirmRevoke', { name })));
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = this.t('cancelAttempt');
    cancel.dataset.focusKey = 'confirm:cancel';
    cancel.addEventListener('click', () => { this.focusNext = confirm.opener; this.confirm = null; this.render(); });
    const go = this.button(confirm.command, turnOff ? 'hTurnOff' : 'hRevokeThis', { danger: true, raw: confirm.raw, id: `confirm:${confirm.command}` });
    if (!turnOff && name) go.setAttribute('aria-label', `${go.textContent ?? ''}: ${name}`);
    box.append(...this.actions([cancel, go]), this.text('p', this.t('systemAsks'), 'hint'));
    return [box];
  }

  private invite(): HTMLElement[] {
    const snapshot = this.snapshot!;
    if (this.limited('inviteState')) return [];
    if (snapshot.inviteState === 'issued') {
      const box = document.createElement('div');
      box.className = 'warning tone-progress invite';
      if (snapshot.expiresAt && !this.limited('expiresAt')) box.append(this.text('p', this.t('inviteExpiresAt', { time: this.time(snapshot.expiresAt) }), 'deadline-text'));
      // H09 gap: no v1 command confirms invalidation, so no Cancel/replace control is offered.
      box.append(this.text('p', this.t('hInviteNoCancel'), 'hint'));
      return [box];
    }
    const notice: Partial<Record<HostSnapshot['inviteState'], Key>> = {
      minting: 'hInvitePreparing', encoding: 'hInvitePreparing', pairing: 'hInvitePreparing',
      refused: 'hInviteRefused', uncertain: 'hInviteUncertain', recovered: 'hInviteRecovered',
      cancelled: 'hInviteCancelled',
    };
    const key = notice[snapshot.inviteState];
    if (key) return [this.notice(this.t(key), 'attention')];
    if (snapshot.inviteState === 'expired') return [this.notice(this.t('hInviteExpired'), 'attention')];
    if (snapshot.inviteState === 'used') return [this.notice(this.t('hInviteUsed'), 'neutral')];
    return [];
  }

  private devices(): HTMLElement[] {
    const snapshot = this.snapshot!;
    const nodes: HTMLElement[] = [this.text('h2', this.t('hDevicesTitle'), 'section-title')];
    const devices = this.limited('devices') ? null : snapshot.devices;
    if (devices === null) { nodes.push(this.notice(this.t('hDevicesUnknown'), 'attention')); return nodes; }
    if (devices.length === 0) { nodes.push(this.text('p', this.t('hDevicesEmpty'))); return nodes; }
    const list = document.createElement('ul');
    list.className = 'computers';
    // Labels are not identities and can repeat: rows carry no actions or selection marker.
    devices.forEach((device) => { list.append(this.text('li', device.label)); });
    nodes.push(list, this.text('p', this.t('hDevicesHint'), 'hint'));
    // Selection is the helper-owned cursor; the web never supplies a device identity.
    const selected = this.selectedLabel();
    const box = document.createElement('div');
    box.className = 'destination selected-device';
    box.append(this.text('span', this.t('hSelectedDevice'), 'muted'), this.text('strong', selected ?? this.t(this.limited('selected') ? 'unknownValue' : 'hNoneSelected')));
    nodes.push(box);
    const next = this.gate('host.nextDevice', () => this.button('host.nextDevice', selected === null ? 'hSelectDevice' : 'hNextDevice'));
    const revoke = selected === null || this.confirm ? null : this.gate('host.revokeDevice', () => {
      const node = this.button('host.revokeDevice', 'hRevokeSelected', { danger: true, raw: selected });
      node.setAttribute('aria-label', `${node.textContent ?? ''}: ${selected}`);
      return node;
    });
    nodes.push(...this.actions([next, revoke]));
    return nodes;
  }

  private body(state: Phase): { badge: [Key, Tone]; title: string; nodes: HTMLElement[] } {
    const phase = state === 'prepared_no_receipt' || state === 'failed_uncertain' || state === 'partial_cleanup'
      || state === 'runtime_unavailable' || state === 'setup_unverified' || state === 'renewal_failed' ? 'failed' : state;
    const snapshot = this.snapshot!;
    const nodes: HTMLElement[] = [];
    const pending = phase === 'ready' ? this.pendingLabel() : null;
    const runtime = snapshot.observation === 'known' && !this.limited('runtime') ? snapshot.runtime : 'unknown';
    const issued = !this.limited('inviteState') && snapshot.inviteState === 'issued';
    let badge: [Key, Tone] = { off: ['hBadgeOff', 'neutral'], pending: ['hBadgeSettingUp', 'progress'], ready: ['hBadgeReady', 'ok'],
      paused: ['hBadgePaused', 'danger'], failed: ['hBadgeRepair', 'attention'], unknown: ['hBadgeUnknown', 'neutral'] }[phase] as [Key, Tone];
    let titleKey: Key = { off: 'hTitleOff', pending: 'hTitlePending', ready: 'hTitleReady', paused: 'hTitlePaused',
      failed: 'hTitleFailed', unknown: 'hTitleUnknown' }[phase] as Key;
    let lead: Key = { off: 'hLeadOff', pending: 'hLeadPending', ready: 'hLeadReady', paused: 'hLeadPaused',
      failed: 'hLeadFailed', unknown: 'hLeadUnknown' }[phase] as Key;
    if (stateCopy[state]) lead = stateCopy[state]!;
    if (this.unconfirmed) lead = 'hLeadUnconfirmed';
    else if (pending !== null) { badge = ['hBadgeApproval', 'attention']; titleKey = 'hTitleRequest'; lead = 'hLeadRequest'; }
    else if (phase === 'ready' && (runtime === 'offline' || runtime === 'restarting')) {
      badge = [runtime === 'offline' ? 'hBadgeOffline' : 'hBadgeRestarting', 'attention'];
      titleKey = runtime === 'offline' ? 'hTitleOffline' : 'hTitleRestarting'; lead = 'hLeadOffline';
    } else if (phase === 'ready' && issued) { badge = ['hBadgeInvite', 'progress']; titleKey = 'hInviteActive'; lead = 'hInviteNative'; }
    nodes.push(this.text('p', this.t(lead)));
    if (!this.unconfirmed && snapshot.observation === 'known' && snapshot.firewallWarning && snapshot.firewallWarning !== 'clear') {
      const key = snapshot.firewallWarning === 'unverified' ? 'firewallUnverified' : 'firewallLikelyBlocked';
      if (Object.hasOwn(catalogs.en, key)) nodes.push(this.notice(this.t(key as Key), 'attention'));
    }
    if (snapshot.observation === 'stale' && !this.limited('observation')) nodes.push(this.notice(this.t('hStale'), 'attention'));
    const outcomeNodes = this.outcomeNodes();
    nodes.push(...outcomeNodes);
    const outcomeCheck = outcomeNodes.some((node) => node.querySelector('[data-focus-key="outcome:check"]'));
    const confirming = this.confirm !== null;
    nodes.push(...this.confirmation());
    const buttons: (HTMLButtonElement | null)[] = [];
    if (['off', 'prepared_no_receipt', 'failed_uncertain', 'runtime_unavailable'].includes(state) && !confirming) buttons.push(this.gate('host.enable', () => this.button('host.enable', 'hTurnOn', { primary: true })));
    if (pending !== null && !confirming) {
      buttons.push(this.gate('host.approveDevice', () => this.button('host.approveDevice', 'hApprove', { primary: true, raw: pending })));
      buttons.push(this.gate('host.rejectDevice', () => this.button('host.rejectDevice', 'hReject', { danger: true, raw: pending })));
      buttons.forEach((node) => { if (node) node.setAttribute('aria-label', `${node.textContent ?? ''}: ${pending}`); });
    }
    if (phase === 'ready') nodes.push(...this.invite());
    if (phase === 'ready' && pending === null && !issued && !confirming) buttons.push(this.gate('host.createInvite', () => this.button('host.createInvite', 'hAddDevice', { primary: true })));
    if ((phase === 'pending' || phase === 'failed' || phase === 'unknown' || phase === 'paused') && !(this.unconfirmed && outcomeCheck)) buttons.push(this.has('host.status') ? this.button('host.status', 'checkAgain') : null);
    nodes.push(...this.actions(buttons));
    if (buttons.some((node) => node && node.dataset.focusKey !== 'host.status:checkAgain')) nodes.push(this.text('p', this.t('systemAsks'), 'hint'));
    if (phase === 'pending') nodes.push(this.text('p', this.t('hCancelNative'), 'hint'));
    if (phase === 'failed' && !stateCopy[state]) nodes.push(this.text('p', this.t('hRepairUnavailable'), 'hint'), this.text('p', this.t('nativeRecoveryHint'), 'hint'));
    if (phase === 'paused') nodes.push(this.text('p', this.t('hRenewUnavailable'), 'hint'));
    if (phase === 'ready' || phase === 'paused') nodes.push(...this.devices());
    if (canTurnOff(state) && !confirming) {
      nodes.push(...this.actions([this.gate('host.turnOff', () => this.button('host.turnOff', 'hTurnOff', { danger: true }))]));
    }
    nodes.push(this.text('p', this.t('hNativeNote'), 'notice native-note'));
    if (this.gated) nodes.push(this.text('p', this.t('capabilityMissing'), 'hint'));
    return { badge, title: this.t(titleKey, { name: pending ?? '' }), nodes };
  }

  render(): void {
    if (!this.snapshot) return;
    // Unconfirmed presents as not confirmed (no lifecycle controls) without
    // changing the retained native snapshot.
    const phase: Phase = this.unconfirmed ? 'unknown' : this.phase();
    const focused = document.activeElement instanceof HTMLElement && this.root.contains(document.activeElement)
      ? document.activeElement.dataset.focusKey ?? (document.activeElement.id === 'host-title' ? 'title' : null) : null;
    this.gated = false;
    const { badge, title, nodes } = this.body(phase);
    const header = document.createElement('div');
    header.className = 'card-header';
    header.append(this.text('span', this.t('hostEyebrow'), 'eyebrow'), this.text('span', this.t(badge[0]), `badge tone-${badge[1]}`));
    const heading = this.text('h2', title, 'card-title');
    heading.id = 'host-title';
    heading.tabIndex = -1;
    const content = document.createElement('div');
    content.className = 'card-body';
    content.append(heading, ...nodes);
    this.root.setAttribute('aria-busy', String(this.pending !== null));
    this.root.replaceChildren(header, content);
    // One polite announcement per phase/record/outcome identity; language switches are silent.
    const outcomeText = nodes.flatMap((node) => [node, ...node.querySelectorAll<HTMLElement>('.notice')])
      .filter((node) => node.dataset.outcome === 'true').map((node) => node.textContent).join(' ');
    // An unconfirmed snapshot's records are not announced as current.
    const selected = this.unconfirmed ? null : this.selectedLabel();
    const outcome = this.outcome;
    const identity = outcome && outcomeText ? `${this.serial}:${outcome.command}:${outcome.result?.effect ?? outcome.error?.code ?? ''}:${outcome.read?.code ?? ''}` : '';
    const key = `${phase}|${badge[0]}|${this.unconfirmed ? '' : this.pendingLabel() ?? ''}|${selected ?? ''}|${this.confirm?.command ?? ''}|${identity}`;
    if (key !== this.announced) {
      this.announced = key;
      const confirmText = this.confirm ? nodes.find((node) => node.classList.contains('confirm'))?.querySelector('strong')?.textContent : null;
      this.live.textContent = [this.t(badge[0]), title, selected ? this.t('hSelectedIs', { name: selected }) : '', confirmText ?? '', outcomeText]
        .filter((part, index, parts) => part && parts.indexOf(part) === index).join('. ');
    }
    const wanted = this.focusNext ?? focused;
    this.focusNext = null;
    if (wanted) {
      const target = wanted === 'title' ? heading : this.root.querySelector<HTMLElement>(`[data-focus-key="${CSS.escape(wanted)}"]`);
      (target ?? heading).focus();
    } else if (this.lastPhase !== null && this.lastPhase !== phase && document.activeElement === document.body) heading.focus();
    this.lastPhase = phase;
  }

  /** Language change: same state, new copy, focus preserved, no native calls. */
  translate(): void { this.render(); }
}
