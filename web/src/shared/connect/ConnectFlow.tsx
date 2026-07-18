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
import { ArrowLeft, Check, ChevronRight, RefreshCw } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import {
  BUILTINS,
  buildConnectPrompt,
  CONFORMANCE_STEPS,
  FIELD_CLASS,
  KINDS,
  NAME_RE,
  useRuntimeConnect,
} from './useRuntimeConnect';
import type { Connected, ConnectMode, Kind } from './useRuntimeConnect';

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
  connectedSubtitle: (via: ConnectMode) => string;
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
              onUseCustom={() => setMode('custom')}
              skipSlot={formSkipSlot}
              waitingSkipSlot={waitingSkipSlot}
            />
          ) : (
            <CustomConnect
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
  const [kind, setKind] = useState<Kind | ''>('');
  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const flow = useRuntimeConnect({
    purpose: 'binary',
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
        Pick the agentic CLI you run — Claude Code, Codex, opencode, or Pi. Paste
        the generated prompt into it: it proves it works and tells HappyRanch
        where its binary lives on this machine so it can launch it.
      </p>

      <div className="mt-6 space-y-2">
        <Label htmlFor="builtin-kind">Pick your agentic CLI</Label>
        <select
          id="builtin-kind"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as Kind | '');
            flow.mint.reset();
          }}
          className={FIELD_CLASS}
        >
          <option value="">Choose an agentic CLI…</option>
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
            ? `Could not generate a prompt (${flow.mint.error.status}).`
            : 'Could not generate a prompt. Is the daemon reachable?'}
        </p>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={!kind || flow.mint.isPending}
          onClick={() => flow.start(kind)}
        >
          {flow.mint.isPending ? 'Generating…' : 'Generate connect prompt'}
        </Button>
        <button
          type="button"
          onClick={onUseCustom}
          className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs underline-offset-2 hover:underline"
        >
          Connect a custom CLI instead
        </button>
        {skipSlot}
      </div>

      <HowThisWorks />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CUSTOM — name → mint profile token → copy prompt → poll for name   */
/* ------------------------------------------------------------------ */

export function CustomConnect({
  onConnected,
  onUseBuiltin,
  skipSlot,
  waitingSkipSlot,
}: {
  onConnected: (c: Connected) => void;
  /** Switch back to the built-in dropdown. Omit on custom-only mounts (e.g.
   *  Settings ▸ Executors, where built-ins live in a separate section) — the
   *  "Connect a built-in CLI instead" toggle then renders nothing. */
  onUseBuiltin?: () => void;
  skipSlot?: ReactNode;
  waitingSkipSlot?: ReactNode;
}): JSX.Element {
  const [nameInput, setNameInput] = useState('');
  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const flow = useRuntimeConnect({
    requirePresent: false,
    via: 'custom',
    onConnected,
  });

  const nameIsBuiltin = BUILTINS.has(nameInput.trim());
  const nameValid = NAME_RE.test(nameInput.trim()) && !nameIsBuiltin;

  const generate = (): void => {
    const name = nameInput.trim();
    if (nameValid) flow.start(name);
  };

  if (flow.state === 'waiting') {
    return (
      <WaitingBody
        name={flow.name}
        prompt={buildConnectPrompt(flow.name, flow.token, origin, 'profile')}
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
        Running a different agentic CLI? HappyRanch connects any conformant CLI.
        Name it, then paste the generated prompt into your CLI — it proves it
        works and tells us how to launch it.
      </p>
      <form
        className="mt-6 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          generate();
        }}
      >
        <Label htmlFor="runtime-name">Name this CLI</Label>
        <p className="text-text-muted -mt-1 text-xs">
          A short identifier for the CLI you&rsquo;re connecting — becomes its
          executor name.
        </p>
        <Input
          id="runtime-name"
          value={nameInput}
          onChange={(e) => {
            setNameInput(e.target.value);
            flow.mint.reset();
          }}
          placeholder="e.g. my-cli"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={nameInput && !nameValid && !flow.mint.isPending ? true : undefined}
        />
        <p className="text-xs">
          {nameInput && nameIsBuiltin ? (
            <span className="text-feedback-danger">
              Pick a name that isn&rsquo;t a built-in (claude, codex, opencode,
              pi) — connect those from the dropdown instead.
            </span>
          ) : nameValid ? (
            <span className="text-feedback-success inline-flex items-center gap-1 font-medium">
              <Check aria-hidden="true" size={13} />
              Lowercase letters, numbers and hyphens
            </span>
          ) : (
            <span className="text-text-muted">
              Lowercase letters, numbers and hyphens · starts with a letter
            </span>
          )}
        </p>
        {flow.mint.isError && (
          <p className="text-feedback-danger text-sm" role="alert">
            {flow.mint.error instanceof ApiError
              ? `Could not generate a prompt (${flow.mint.error.status}).`
              : 'Could not generate a prompt. Is the daemon reachable?'}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-3">
          <Button type="submit" disabled={!nameValid || flow.mint.isPending}>
            {flow.mint.isPending ? 'Generating…' : 'Generate connect prompt'}
          </Button>
          {onUseBuiltin && (
            <button
              type="button"
              onClick={onUseBuiltin}
              className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs underline-offset-2 hover:underline"
            >
              <ArrowLeft aria-hidden="true" size={14} />
              Connect a built-in CLI instead
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
  const [copied, setCopied] = useState(false);
  const copy = (): void => {
    void navigator.clipboard?.writeText(prompt);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="mt-6 max-w-2xl">
      {/* Prompt block — dot header + mono caption + Copy, mono body. */}
      <div className="border-border-default bg-surface shadow-pasture-sm overflow-hidden rounded-lg border">
        <div className="border-border-default bg-surface-sunken flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="flex gap-1.5">
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
              <span className="bg-border-strong h-2 w-2 rounded-full" />
            </span>
            <span className="text-text-muted font-mono text-xs">
              connect prompt · paste into your agentic CLI
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
              Copied
            </>
          ) : (
            <>
              <CopyGlyph />
              Copy prompt
            </>
          )}
        </Button>
        <span className="text-text-muted text-xs">
          Then run it in your terminal — this screen updates live.
        </span>
      </div>

      {/* Detect strip — driven by the prereqs poll (single honest signal). */}
      {expired ? (
        <div className="border-feedback-warning/30 bg-feedback-warning/5 mt-6 rounded-lg border p-4">
          <p className="text-text-primary text-sm font-semibold">
            This link expired
          </p>
          <p className="text-text-muted mt-1 text-xs">
            The prompt is valid for about 10 minutes and this one lapsed before a
            CLI connected. Nothing was lost — regenerate a fresh prompt.
          </p>
          <div className="mt-3">
            <Button variant="outline" onClick={onRegenerate} disabled={regenerating}>
              <RefreshCw aria-hidden="true" size={15} />
              {regenerating ? 'Regenerating…' : 'Regenerate prompt'}
            </Button>
          </div>
        </div>
      ) : (
        <div
          aria-label="Waiting for your CLI"
          className="border-border-default bg-surface mt-6 rounded-lg border p-4"
        >
          <div className="flex items-center gap-2">
            <Spinner className="text-accent h-4 w-4" />
            <p className="text-text-primary text-sm font-medium">
              Waiting for <span className="font-mono">{name}</span> to connect…
            </p>
          </div>
          <p className="text-text-muted mt-1 text-xs">
            As your CLI runs the prompt, it completes these checks, then
            registers:
          </p>
          <ul className="mt-3 space-y-1.5">
            {CONFORMANCE_STEPS.map((s) => (
              <li key={s.id} className="flex items-center gap-2.5">
                <span
                  aria-hidden="true"
                  className="border-border-strong h-4 w-4 shrink-0 rounded-full border"
                />
                <span className="text-text-secondary text-sm">{s.label}</span>
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
          Back to the prompt
        </button>
        {skipSlot}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Connected — name (FE-known) + registered path (register-real)      */
/* ------------------------------------------------------------------ */

export function ConnectedCard({
  connected,
  subtitle,
  primaryAction,
  onReset,
}: {
  connected: Connected;
  subtitle: (via: ConnectMode) => string;
  primaryAction?: ReactNode;
  onReset: () => void;
}): JSX.Element {
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
          <span className="font-mono">{connected.name}</span> connected.
        </h1>
      </div>
      <p className="text-text-secondary mt-3 max-w-lg text-base leading-relaxed">
        {subtitle(connected.via)}
      </p>

      <div className="bg-surface border-border-default shadow-pasture-sm mt-6 max-w-lg rounded-lg border p-4">
        <p className="text-text-muted text-caption font-semibold tracking-wider uppercase">
          Name
        </p>
        <p className="text-text-primary mt-1 font-mono text-sm font-medium">
          {connected.name}
        </p>
        <p className="text-text-muted text-caption mt-3 font-semibold tracking-wider uppercase">
          Registered at
        </p>
        <p className="text-text-secondary mt-1 truncate font-mono text-xs">
          {connected.path ?? 'on PATH'}
        </p>
      </div>

      <div className="mt-6 flex items-center gap-2">
        {primaryAction}
        <Button variant="outline" onClick={onReset}>
          Connect another
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
  return (
    <details className="group mt-5">
      <summary className="text-text-secondary hover:text-text-primary flex cursor-pointer list-none items-center gap-1.5 text-xs">
        <ChevronRight
          aria-hidden="true"
          size={14}
          className="transition-transform group-open:rotate-90"
        />
        How this works
      </summary>
      <p className="text-text-muted mt-2 max-w-lg pl-5 text-xs leading-relaxed">
        The prompt carries a short-lived, scoped token valid for about{' '}
        <span className="text-text-secondary font-medium">10 minutes</span>.
        Copying doesn&rsquo;t run anything — nothing executes on your machine
        until you paste and run it yourself. Connecting only makes the CLI
        available to choose; assigning an agent to run on it is a separate,
        later step.
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
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-text-secondary hover:text-text-primary hover:border-border-strong border-border-default inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors"
    >
      {copied ? (
        <>
          <Check aria-hidden="true" size={13} className="text-feedback-success" />
          Copied
        </>
      ) : (
        <>
          <CopyGlyph />
          Copy
        </>
      )}
    </button>
  );
}

/** Copy glyph (kept local, mirrors the one in OnboardingPage). */
function CopyGlyph(): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-3.5 w-3.5"
    >
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

/** Ring spinner (mirrors OnboardingPage's). */
function Spinner({ className }: { className?: string }): JSX.Element {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block animate-spin rounded-full border-2 border-current border-t-transparent ${className ?? ''}`}
    />
  );
}
