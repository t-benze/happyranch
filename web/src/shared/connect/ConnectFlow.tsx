/**
 * ConnectFlow — the CHROME-FREE connect UI shared by onboarding and Settings ▸
 * Executors (THR-107). It owns the connect flow ONLY: mode toggle (built-in
 * dropdown vs custom name) → scoped token mint → copy-paste prompt → live
 * GET /health/prereqs poll → connected card. The mint/poll engine and the
 * prompt builder live in ./useRuntimeConnect.
 *
 * Onboarding-only chrome (the step eyebrow, the wizard headings, and the
 * Continue/Skip navigation) is NOT rendered here — consumers inject it via the
 * slot props below so it never leaks into the shared surface. Onboarding wraps
 * this with its <StepEyebrow/> + step heading + Continue/Skip; Settings will
 * wrap it with its own panel heading + a "back to list" action.
 *
 * See ./useRuntimeConnect and the original THR-088 ConnectRuntimeStep header
 * for the honesty-fence rationale (scoped tokens only, no invented status, the
 * connected card shows only register-real data).
 */
import { useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, ArrowLeft, Check, ChevronRight, RefreshCw } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { useTranslation } from '@/hooks/i18n';
import {
  BUILTINS,
  buildConnectPrompt,
  buildDirectConnectPrompt,
  CONFORMANCE_STEPS,
  FIELD_CLASS,
  KINDS,
  NAME_RE,
  useRuntimeConnect,
  useDirectConnect,
} from './useRuntimeConnect';
import type { Connected, ConnectMode, Kind } from './useRuntimeConnect';
import { Spinner } from './Spinner';

interface ConnectFlowProps {
  /** Outer wrapper class. Onboarding passes its page-section spacing. */
  className?: string;
  /** Slot rendered at the top of every state. Onboarding: <StepEyebrow/>. */
  eyebrow?: ReactNode;
  /** Heading above the connect bodies in the form/waiting state. Onboarding:
   *  its <h1>Connect your agentic CLI.</h1>. */
  formHeading?: ReactNode;
  /** Skip affordance rendered in the mode-body actions row (onboarding wizard
   *  navigation — omitted on management surfaces). */
  formSkipSlot?: ReactNode;
  /** Skip affordance rendered in the waiting-body footer. */
  waitingSkipSlot?: ReactNode;
  /** Connected-card subtitle copy, keyed on the originating flow. */
  connectedSubtitle: (via: ConnectMode) => ReactNode;
  /** Primary action rendered before "Connect another" on the connected card
   *  (onboarding: Continue → Step 2). Omit for none. */
  connectedPrimaryAction?: ReactNode;
}

export function ConnectFlow({
  className,
  eyebrow,
  formHeading,
  formSkipSlot,
  waitingSkipSlot,
  connectedSubtitle,
  connectedPrimaryAction,
}: ConnectFlowProps): JSX.Element {
  const [mode, setMode] = useState<ConnectMode>('builtin');
  const [connected, setConnected] = useState<Connected | null>(null);

  const switchToCustom = (): void => {
    setMode('custom');
  };

  return (
    <div className={className}>
      {eyebrow}
      {connected ? (
        <ConnectedCard
          connected={connected}
          subtitle={connectedSubtitle}
          primaryAction={connectedPrimaryAction}
          onReset={() => setConnected(null)}
        />
      ) : (
        <>
          {formHeading}
          {mode === 'builtin' ? (
            <BuiltinConnect
              onConnected={setConnected}
              onUseCustom={switchToCustom}
              skipSlot={formSkipSlot}
              waitingSkipSlot={waitingSkipSlot}
            />
          ) : (
            <AdapterConnect
              onConnected={setConnected}
              onUseBuiltin={() => setMode('builtin')}
              skipSlot={formSkipSlot}
              waitingSkipSlot={waitingSkipSlot}
            />
          )}
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BUILT-IN — dropdown → mint binary token → copy prompt → poll       */
/* ------------------------------------------------------------------ */

export function BuiltinConnect({
  onConnected,
  onUseCustom,
  skipSlot,
  waitingSkipSlot,
}: {
  onConnected: (c: Connected) => void;
  onUseCustom: () => void;
  skipSlot?: ReactNode;
  waitingSkipSlot?: ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const [kind, setKind] = useState<Kind | ''>('');
  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const flow = useRuntimeConnect({
    requirePresent: true,
    via: 'builtin',
    onConnected,
  });

  if (flow.state === 'waiting') {
    return (
      <WaitingBody
        name={flow.name}
        prompt={buildConnectPrompt(flow.name, flow.token, origin, 'binary')}
        expired={flow.expired}
        regenerating={flow.mint.isPending}
        onRegenerate={flow.regenerate}
        onBack={flow.back}
        skipSlot={waitingSkipSlot}
      />
    );
  }

  return (
    <div className="mt-6 max-w-lg">
      <p className="text-text-secondary text-base leading-relaxed">
        {t('onboarding.connect.builtin.intro')}
      </p>

      <div className="mt-6 space-y-2">
        <Label htmlFor="builtin-kind">{t('onboarding.connect.builtin.label')}</Label>
        <select
          id="builtin-kind"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as Kind | '');
            flow.mint.reset();
          }}
          className={FIELD_CLASS}
        >
          <option value="">{t('onboarding.connect.builtin.placeholder')}</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </div>

      {flow.mint.isError && (
        <p className="text-feedback-danger mt-3 text-sm" role="alert">
          {flow.mint.error instanceof ApiError
            ? t('onboarding.connect.mintError.status', { status: flow.mint.error.status })
            : t('onboarding.connect.mintError.unreachable')}
        </p>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={!kind || flow.mint.isPending}
          onClick={() => flow.start(kind)}
        >
          {flow.mint.isPending
            ? t('onboarding.connect.generating')
            : t('onboarding.connect.generate')}
        </Button>
        <Button
          type="button"
          onClick={onUseCustom}
        >
          {t('onboarding.connect.useCustom')}
        </Button>
        {skipSlot}
      </div>

      <HowThisWorks />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ADAPTER-BACKED — name → mint adapter token → create/submit wrapper   */
/*  → poll adapter status → auto-bind when APPROVED                       */
/* ------------------------------------------------------------------ */

export function AdapterConnect({
  onConnected,
  onUseBuiltin,
  skipSlot,
  waitingSkipSlot,
}: {
  onConnected: (c: Connected) => void;
  onUseBuiltin?: () => void;
  skipSlot?: ReactNode;
  waitingSkipSlot?: ReactNode;
}): JSX.Element {
  const { t, render } = useTranslation();
  const [nameInput, setNameInput] = useState('');
  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const flow = useDirectConnect({ onConnected });

  const nameIsBuiltin = BUILTINS.has(nameInput.trim());
  const nameValid = NAME_RE.test(nameInput.trim()) && !nameIsBuiltin;

  const generate = (): void => {
    const name = nameInput.trim();
    if (nameValid) flow.start(name);
  };

  // Waiting state — the candidate CLI creates the wrapper and POSTs /connect
  if (flow.state.stage === 'waiting') {
    const s = flow.state;
    return (
      <AdapterWaitingBody
        name={s.name}
        prompt={buildDirectConnectPrompt(s.name, s.token, origin, s.wrapperDestination)}
        expired={s.expired}
        regenerating={flow.mint.isPending}
        onRegenerate={flow.regenerate}
        onBack={flow.back}
        skipSlot={waitingSkipSlot}
      />
    );
  }

  // Committing state — the receipt landed; finishing the connection.
  if (flow.state.stage === 'committing') {
    return (
      <div className="mt-6 max-w-lg">
        <div className="border-accent/30 bg-accent/5 rounded-lg border p-4">
          <div className="flex items-center gap-2">
            <Spinner className="text-accent h-4 w-4" />
            <p className="text-text-primary text-sm font-medium">
              {t('onboarding.connect.committing.title')}
            </p>
          </div>
          <p className="text-text-secondary mt-2 text-xs">
            {render('onboarding.connect.committing.body', {
              name: <span className="font-mono">{flow.state.name}</span>,
            })}
          </p>
        </div>
      </div>
    );
  }

  // Retryable failure — first conformance-probe failure. The user must change
  // the wrapper/artifacts and rerun the EXISTING prompt before expiry. No
  // token replay, no /retry, no /forget prerequisite.
  if (flow.state.stage === 'failed_retryable') {
    const s = flow.state;
    return (
      <AdapterRetryableBody
        name={s.name}
        prompt={buildDirectConnectPrompt(s.name, s.token, origin, s.wrapperDestination)}
        reason={s.reason}
        onRerun={flow.rerunExistingPrompt}
        onBack={flow.back}
      />
    );
  }

  // Failed state — terminal nonretryable, expired, or exhausted. The dedicated
  // /retry action is the historical immutable-snapshot validation path, not an
  // artifact retry.
  if (flow.state.stage === 'failed') {
    return (
      <AdapterBindFailedBody
        name={flow.state.name}
        adapterId={flow.state.operationId}
        error={flow.state.reason}
        category={flow.state.category}
        onRetry={flow.retryValidation}
        onBack={flow.back}
        onClear={flow.clearFailed}
        isClearing={flow.isClearing}
        clearError={flow.forgetError}
      />
    );
  }

  if (flow.state.stage === 'cleared') {
    return <FailedConnectionClearedBody name={flow.state.name} wrapperStatus={flow.state.wrapperStatus} onReconnect={flow.back} />;
  }

  // Connected state
  if (flow.state.stage === 'connected') {
    return (
      <ConnectedCard
        connected={{ name: flow.state.name, path: flow.state.wrapperDestination, via: 'custom' }}
        subtitle={(via) =>
          via === 'custom'
            ? t('onboarding.connect.connected.adapterSubtitle')
            : ''
        }
        onReset={flow.back}
      />
    );
  }

  // Form state

  return (
    <div className="mt-6 max-w-lg">
      <div className="border-accent/30 bg-accent/5 mb-4 rounded-r-md border-l-2 px-3 py-2">
        <p className="text-text-primary text-sm font-medium">
          {t('onboarding.connect.adapter.bannerTitle')}
        </p>
        <p className="text-text-secondary mt-0.5 text-xs">
          {t('onboarding.connect.adapter.bannerBody')}
        </p>
      </div>
      <form
        className="mt-6 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          generate();
        }}
      >
        <Label htmlFor="adapter-name">{t('onboarding.connect.adapter.nameLabel')}</Label>
        <p className="text-text-muted -mt-1 text-xs">
          {render('onboarding.connect.adapter.nameHint', {
            code: <code className="font-mono">&lt;name&gt;-adapter</code>,
          })}
        </p>
        <Input
          id="adapter-name"
          value={nameInput}
          onChange={(e) => {
            setNameInput(e.target.value);
            flow.mint.reset();
          }}
          placeholder={t('onboarding.connect.adapter.namePlaceholder')}
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={nameInput && !nameValid && !flow.mint.isPending ? true : undefined}
        />
        <p className="text-xs">
          {nameInput && nameIsBuiltin ? (
            <span className="text-feedback-danger">
              {t('onboarding.connect.adapter.builtinName')}
            </span>
          ) : nameValid ? (
            <span className="text-feedback-success inline-flex items-center gap-1 font-medium">
              <Check aria-hidden="true" size={13} />
              {t('onboarding.create.slugRule')}
            </span>
          ) : (
            <span className="text-text-muted">
              {t('onboarding.connect.adapter.nameRule')}
            </span>
          )}
        </p>

        {flow.mint.isError && (
          <p className="text-feedback-danger text-sm" role="alert">
            {flow.mint.error instanceof ApiError
              ? t('onboarding.connect.mintError.status', { status: flow.mint.error.status })
              : t('onboarding.connect.mintError.unreachable')}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-3">
          <Button type="submit" disabled={!nameValid || flow.mint.isPending}>
            {flow.mint.isPending
              ? t('onboarding.connect.generating')
              : t('onboarding.connect.generate')}
          </Button>
          {onUseBuiltin && (
            <button
              type="button"
              onClick={onUseBuiltin}
              className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs underline-offset-2 hover:underline"
            >
              <ArrowLeft aria-hidden="true" size={14} />
              {t('onboarding.connect.useBuiltin')}
            </button>
          )}
          {skipSlot}
        </div>
        <HowThisWorks />
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Shared waiting body — prompt block + copy + live detect strip      */
/* ------------------------------------------------------------------ */

export function WaitingBody({
  name,
  prompt,
  expired,
  regenerating,
  onRegenerate,
  onBack,
  skipSlot,
}: {
  name: string;
  prompt: string;
  expired: boolean;
  regenerating: boolean;
  onRegenerate: () => void;
  onBack: () => void;
  skipSlot?: ReactNode;
}): JSX.Element {
  const { t, render } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard?.writeText(prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="mt-6 max-w-2xl">
      <div className="border-border-default bg-surface shadow-pasture-sm overflow-hidden rounded-lg border">
        <div className="border-border-default bg-surface-sunken flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="flex gap-1.5">
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
            </span>
            <span className="text-text-muted font-mono text-xs">
              {t('onboarding.connect.waiting.header')}
            </span>
          </div>
          <CopyButton copied={copied} onClick={copy} />
        </div>
        <pre className="text-text-secondary overflow-x-auto px-4 py-4 font-mono text-xs leading-relaxed whitespace-pre">
          {prompt}
        </pre>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={copy}>
          {copied ? (
            <>
              <Check aria-hidden="true" />
              {t('onboarding.connect.copied')}
            </>
          ) : (
            <>
              <CopyGlyph />
              {t('onboarding.connect.copyPrompt')}
            </>
          )}
        </Button>
        <span className="text-text-muted text-xs">
          {t('onboarding.connect.waiting.hint')}
        </span>
      </div>

      {expired ? (
        <div className="border-feedback-warning/30 bg-feedback-warning/5 mt-6 rounded-lg border p-4">
          <p className="text-text-primary text-sm font-semibold">
            {t('onboarding.connect.expired.title')}
          </p>
          <p className="text-text-muted mt-1 text-xs">
            {t('onboarding.connect.expired.body')}
          </p>
          <div className="mt-3">
            <Button variant="outline" onClick={onRegenerate} disabled={regenerating}>
              <RefreshCw aria-hidden="true" size={15} />
              {regenerating
                ? t('onboarding.connect.regenerating')
                : t('onboarding.connect.regenerate')}
            </Button>
          </div>
        </div>
      ) : (
        <div
          aria-label={t('onboarding.connect.waiting.aria')}
          className="border-border-default bg-surface mt-6 rounded-lg border p-4"
        >
          <div className="flex items-center gap-2">
            <Spinner className="text-accent h-4 w-4" />
            <p className="text-text-primary text-sm font-medium">
              {render('onboarding.connect.waiting.title', {
                name: <span className="font-mono">{name}</span>,
              })}
            </p>
          </div>
          <p className="text-text-muted mt-1 text-xs">
            {t('onboarding.connect.waiting.stepsLead')}
          </p>
          <ul className="mt-3 space-y-1.5">
            {CONFORMANCE_STEPS.map((s) => (
              <li key={s.id} className="flex items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className="border-border-strong h-4 w-4 shrink-0 rounded-full border"
                />
                <span className="text-text-secondary text-sm">{t(s.labelKey)}</span>
                <span className="text-text-muted font-mono text-xs">{s.id}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5 flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs"
        >
          <ArrowLeft aria-hidden="true" size={14} />
          {t('onboarding.connect.back')}
        </button>
        {skipSlot}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Adapter-specific waiting — adapter-wrapper prompt + live status    */
/* ------------------------------------------------------------------ */

export function AdapterWaitingBody({
  name: _name,
  prompt,
  expired,
  regenerating,
  onRegenerate,
  onBack,
  skipSlot,
}: {
  name: string;
  prompt: string;
  expired: boolean;
  regenerating: boolean;
  onRegenerate: () => void;
  onBack: () => void;
  skipSlot?: ReactNode;
}): JSX.Element {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard?.writeText(prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="mt-6 max-w-2xl">
      <div className="border-border-default bg-surface shadow-pasture-sm overflow-hidden rounded-lg border">
        <div className="border-border-default bg-surface-sunken flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="flex gap-1.5">
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
            </span>
            <span className="text-text-muted font-mono text-xs">
              {t('onboarding.connect.adapter.waiting.header')}
            </span>
          </div>
          <CopyButton copied={copied} onClick={copy} />
        </div>
        <pre className="text-text-secondary overflow-x-auto px-4 py-4 font-mono text-xs leading-relaxed whitespace-pre">
          {prompt}
        </pre>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={copy}>
          {copied ? (
            <>
              <Check aria-hidden="true" />
              {t('onboarding.connect.copied')}
            </>
          ) : (
            <>
              <CopyGlyph />
              {t('onboarding.connect.copyPrompt')}
            </>
          )}
        </Button>
        <span className="text-text-muted text-xs">
          {t('onboarding.connect.adapter.waiting.hint')}
        </span>
      </div>

      {expired ? (
        <div className="border-feedback-warning/30 bg-feedback-warning/5 mt-6 rounded-lg border p-4">
          <p className="text-text-primary text-sm font-semibold">
            {t('onboarding.connect.expired.title')}
          </p>
          <p className="text-text-muted mt-1 text-xs">
            {t('onboarding.connect.adapter.expired.body')}
          </p>
          <div className="mt-3">
            <Button variant="outline" onClick={onRegenerate} disabled={regenerating}>
              <RefreshCw aria-hidden="true" size={15} />
              {regenerating
                ? t('onboarding.connect.regenerating')
                : t('onboarding.connect.regenerate')}
            </Button>
          </div>
        </div>
      ) : (
        <div
          aria-label={t('onboarding.connect.adapter.waiting.aria')}
          className="border-border-default bg-surface mt-6 rounded-lg border p-4"
        >
          <div className="flex items-center gap-2">
            <Spinner className="text-accent h-4 w-4" />
            <p className="text-text-primary text-sm font-medium">
              {t('onboarding.connect.adapter.waiting.title')}
            </p>
          </div>
          <p className="text-text-muted mt-1 text-xs">
            {t('onboarding.connect.adapter.waiting.body')}
          </p>
        </div>
      )}

      <div className="mt-5 flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs"
        >
          <ArrowLeft aria-hidden="true" size={14} />
          {t('onboarding.connect.back')}
        </button>
        {skipSlot}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Adapter retryable failure — first conformance-probe failure        */
/* ------------------------------------------------------------------ */

export function AdapterRetryableBody({
  name,
  prompt,
  reason,
  onRerun,
  onBack,
}: {
  name: string;
  prompt: string;
  reason: string;
  onRerun: () => void;
  onBack: () => void;
}): JSX.Element {
  const { t, render } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard?.writeText(prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="mt-6 max-w-2xl">
      <div className="border-feedback-warning/30 bg-feedback-warning/5 mb-4 rounded-lg border p-4">
        <div className="flex items-center gap-2">
          <AlertTriangle className="text-feedback-warning h-4 w-4" />
          <p className="text-text-primary text-sm font-medium">
            {t('onboarding.connect.retryable.title')}
          </p>
        </div>
        <p className="text-text-secondary mt-2 text-xs">
          {render('onboarding.connect.retryable.body', {
            name: <span className="font-mono">{name}</span>,
          })}
        </p>
        <div className="bg-surface-sunken mt-3 rounded p-3">
          <p className="text-feedback-danger font-mono text-xs">
            {t('onboarding.connect.error.label', { detail: reason })}
          </p>
        </div>
      </div>

      <div className="border-border-default bg-surface shadow-pasture-sm overflow-hidden rounded-lg border">
        <div className="border-border-default bg-surface-sunken flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="flex gap-1.5">
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
            </span>
            <span className="text-text-muted font-mono text-xs">
              {t('onboarding.connect.retryable.header')}
            </span>
          </div>
          <CopyButton copied={copied} onClick={copy} />
        </div>
        <pre className="text-text-secondary overflow-x-auto px-4 py-4 font-mono text-xs leading-relaxed whitespace-pre">
          {prompt}
        </pre>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={copy}>
          {copied ? (
            <>
              <Check aria-hidden="true" />
              {t('onboarding.connect.copied')}
            </>
          ) : (
            <>
              <CopyGlyph />
              {t('onboarding.connect.copyPrompt')}
            </>
          )}
        </Button>
        <Button variant="outline" onClick={onRerun}>
          <RefreshCw aria-hidden="true" size={15} />
          {t('onboarding.connect.retryable.rerun')}
        </Button>
      </div>

      <div className="mt-5 flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs"
        >
          <ArrowLeft aria-hidden="true" size={14} />
          {t('onboarding.connect.back')}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Adapter connect failed — receipt landed, projection failed         */
/* ------------------------------------------------------------------ */

export function AdapterBindFailedBody({
  name,
  adapterId,
  error,
  category,
  onRetry,
  onBack,
  onClear,
  isClearing,
  clearError,
}: {
  name: string;
  /** The direct-connect operation id (not an adapter id, despite the prop
   *  name — kept stable to avoid an unrelated churn in this component). */
  adapterId: string;
  error: string;
  category: 'nonretryable' | 'expired' | 'exhausted';
  onRetry: () => void;
  onBack: () => void;
  onClear: () => void;
  isClearing: boolean;
  clearError: unknown;
}): JSX.Element {
  const { t, render } = useTranslation();
  const [confirmingClear, setConfirmingClear] = useState(false);
  const clearErrorMessage = clearError instanceof ApiError
    ? (typeof clearError.detail === 'string' ? clearError.detail : clearError.message)
    : clearError instanceof Error ? clearError.message : null;
  const categoryMessageKey = {
    expired: 'onboarding.connect.failed.category.expired',
    exhausted: 'onboarding.connect.failed.category.exhausted',
    nonretryable: 'onboarding.connect.failed.category.nonretryable',
  } as const;
  const categoryMessage = t(categoryMessageKey[category]);
  return (
    <div className="mt-6 max-w-lg">
      <div className="border-feedback-danger/30 bg-feedback-danger/5 rounded-lg border p-4">
        <div className="flex items-center gap-2">
          <AlertTriangle className="text-feedback-danger h-4 w-4" />
          <p className="text-text-primary text-sm font-medium">
            {t('onboarding.connect.failed.title')}
          </p>
        </div>
        <p className="text-text-secondary mt-2 text-xs">
          {render('onboarding.connect.failed.body', {
            name: <span className="font-mono">{name}</span>,
            categoryMessage,
          })}
        </p>
        <div className="bg-surface-sunken mt-3 rounded p-3">
          <p className="text-text-muted font-mono text-xs">
            {t('onboarding.connect.failed.operation', { operationId: adapterId })}
          </p>
          <p className="text-feedback-danger mt-1 font-mono text-xs">
            {t('onboarding.connect.error.label', { detail: error })}
          </p>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button onClick={onRetry} disabled={isClearing}>
            <RefreshCw aria-hidden="true" size={15} />
            {t('onboarding.connect.validate')}
          </Button>
          <Button variant="outline" onClick={onBack}>
            <ArrowLeft aria-hidden="true" size={14} />
            {t('onboarding.connect.backShort')}
          </Button>
          {confirmingClear ? (
            <>
              <Button variant="destructive" onClick={onClear} disabled={isClearing}>
                {isClearing
                  ? t('onboarding.connect.clearing')
                  : t('onboarding.connect.clear.confirm')}
              </Button>
              <Button variant="secondary" onClick={() => setConfirmingClear(false)} disabled={isClearing}>
                {t('common.cancel')}
              </Button>
            </>
          ) : (
            <Button variant="secondary" onClick={() => setConfirmingClear(true)}>
              {t('onboarding.connect.clear.action')}
            </Button>
          )}
        </div>
        <p className="text-text-muted mt-3 text-xs">
          {t('onboarding.connect.clear.note')}
        </p>
        {clearErrorMessage && <p className="text-feedback-danger mt-2 text-sm" role="alert">{clearErrorMessage}</p>}
      </div>
    </div>
  );
}

export function FailedConnectionClearedBody({
  name,
  wrapperStatus,
  onReconnect,
}: {
  name: string;
  wrapperStatus: import('@/lib/api/directConnect').ForgetWrapperStatus;
  onReconnect: () => void;
}): JSX.Element {
  const { t, render } = useTranslation();
  const wrapperMessageKey = {
    already_absent: 'onboarding.connect.cleared.alreadyAbsent',
    preserved_changed: 'onboarding.connect.cleared.preservedChanged',
    preserved_unsafe: 'onboarding.connect.cleared.preservedUnsafe',
  } as const;
  return (
    <div className="mt-6 max-w-lg border-border-default bg-surface rounded-lg border p-4">
      <p className="text-text-primary text-sm font-medium">{t('onboarding.connect.cleared.title')}</p>
      <p className="text-text-secondary mt-2 text-sm">
        {render('onboarding.connect.cleared.body', {
          name: <span className="font-mono">{name}</span>,
        })}
      </p>
      <p className="text-text-secondary mt-1 text-sm">{t(wrapperMessageKey[wrapperStatus])}</p>
      <Button className="mt-4" onClick={onReconnect}>{t('onboarding.connect.cleared.reconnect')}</Button>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Connected card                                                      */
/* ------------------------------------------------------------------ */

export function ConnectedCard({
  connected,
  subtitle,
  primaryAction,
  onReset,
}: {
  connected: Connected;
  subtitle: (via: ConnectMode) => ReactNode;
  primaryAction?: ReactNode;
  onReset: () => void;
}): JSX.Element {
  const { t, render } = useTranslation();
  return (
    <>
      <div className="mt-3 flex items-center gap-3">
        <span
          aria-hidden="true"
          className="bg-feedback-success/15 text-feedback-success inline-flex h-10 w-10 items-center justify-center rounded-full"
        >
          <Check size={22} />
        </span>
        <h1 className="font-display text-h1 text-text-primary font-medium">
          {render('onboarding.connect.connected.title', {
            name: <span className="font-mono">{connected.name}</span>,
          })}
        </h1>
      </div>
      <p className="text-text-secondary mt-3 max-w-lg text-base leading-relaxed">
        {subtitle(connected.via)}
      </p>

      <div className="bg-surface border-border-default shadow-pasture-sm mt-6 max-w-lg rounded-lg border p-4">
        <p className="text-text-muted text-caption font-semibold tracking-wider uppercase">
          {t('onboarding.connect.connected.name')}
        </p>
        <p className="text-text-primary mt-1 font-mono text-sm font-medium">
          {connected.name}
        </p>
        <p className="text-text-muted text-caption mt-3 font-semibold tracking-wider uppercase">
          {t('onboarding.connect.connected.registeredAt')}
        </p>
        <p className="text-text-secondary mt-1 truncate font-mono text-xs">
          {connected.path ?? t('onboarding.connect.connected.registrationRequired')}
        </p>
      </div>

      <div className="mt-6 flex items-center gap-2">
        {primaryAction}
        <Button variant="outline" onClick={onReset}>
          {t('onboarding.connect.connectAnother')}
        </Button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Small shared bits                                                  */
/* ------------------------------------------------------------------ */

/** Honesty note shared by both copy-paste flows. */
function HowThisWorks(): JSX.Element {
  const { t, render } = useTranslation();
  return (
    <details className="group mt-5">
      <summary className="text-text-secondary hover:text-text-primary flex cursor-pointer list-none items-center gap-1.5 text-xs">
        <ChevronRight
          aria-hidden="true"
          size={14}
          className="transition-transform group-open:rotate-90"
        />
        {t('onboarding.connect.how.title')}
      </summary>
      <p className="text-text-muted mt-2 max-w-lg pl-5 text-xs leading-relaxed">
        {render('onboarding.connect.how.body', {
          duration: (
            <span className="text-text-secondary font-medium">
              {t('onboarding.connect.how.duration')}
            </span>
          ),
        })}
      </p>
    </details>
  );
}

function CopyButton({
  copied,
  onClick,
}: {
  copied: boolean;
  onClick: () => void;
}): JSX.Element {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-text-secondary hover:text-text-primary hover:border-border-strong border-border-default inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors"
    >
      {copied ? (
        <>
          <Check aria-hidden="true" size={13} className="text-feedback-success" />
          {t('onboarding.connect.copied')}
        </>
      ) : (
        <>
          <CopyGlyph />
          {t('onboarding.connect.copy')}
        </>
      )}
    </button>
  );
}

function CopyGlyph(): JSX.Element {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 15 15"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <rect x="1" y="5" width="7" height="9" rx="1" stroke="currentColor" />
      <path
        d="M5 4V3a1 1 0 011-1h6a1 1 0 011 1v7a1 1 0 01-1 1h-1"
        stroke="currentColor"
      />
    </svg>
  );
}
