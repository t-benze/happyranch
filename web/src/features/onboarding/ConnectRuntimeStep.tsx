/**
 * ConnectRuntimeStep — THR-088 onboarding Step 1 of 2: "Connect your agentic
 * CLI". Two flows behind one step, now sharing ONE copy-paste + live-poll UX:
 *
 *   BUILT-IN — dropdown of built-in kinds (claude / codex / opencode / pi from
 *   EXECUTOR_BINARY_KINDS). The user picks a kind, mints a KIND-SCOPED,
 *   purpose='binary' registration token, and pastes the generated prompt into
 *   that CLI. The CLI completes the conformance challenge and POSTs its OWN
 *   absolute binary path to POST /executors/runtime/register-binary (the kind
 *   is carried by the token, NOT in the body). Registering = recording the
 *   BINARY PATH in the machine-local executor-binaries registry.
 *
 *   CUSTOM — same shape, but the pasted prompt self-registers a PROFILE via
 *   POST /executors/runtime/register (purpose='profile'): command,
 *   argv_template and adapter. There is deliberately NO `/connect` one-click
 *   route (founder ruling).
 *
 * The ONLY difference between the flows is that register target and the token
 * purpose the backend fence keys on: built-in → register-binary
 * (purpose='binary'), custom → register (purpose='profile'). A binary-purpose
 * token CANNOT self-register a profile (the /register fence rejects it), so the
 * built-in prompt can never mint a profile. Everything else — the mint, the
 * copy-paste prompt component, the live poll, the connected card — is shared.
 *
 * CONNECT SIGNAL — GET /health/prereqs. Since #400/#420 `present` is TRUE for a
 * kind ONLY after it is registered in the machine-local binary registry
 * (get_binary valid) — being on PATH is NOT sufficient. So the built-in poll
 * predicate `p.tool === name && p.present` is registration-gated and cannot
 * false-positive on a merely-detected CLI. (This supersedes the earlier note
 * that built-ins are "always in prereqs so the poll false-positives".) A custom
 * PROFILE writes no binary, so its `present` stays false; a custom profile
 * instead only APPEARS in prereqs once it registers, and that appearance is its
 * connect signal — hence `requirePresent` is true for built-in, false for
 * custom (see useRuntimeConnect).
 *
 * Honesty fence (THR-061 §D; THR-088): scoped tokens only; no invented status.
 * The connected card shows the kind/name (FE-known) + the registered PATH
 * (register-real). The conformance checks render as the described SEQUENCE, not
 * live-ticking badges (prereqs can't report per-step progress).
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Check, ChevronRight, RefreshCw } from 'lucide-react';
import {
  ApiError,
  executorBinaries,
  health as healthApi,
  settings as settingsApi,
} from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';

/** The built-in executor kinds, derived from the api client's canonical list. */
const KINDS = executorBinaries.EXECUTOR_BINARY_KINDS;
type Kind = (typeof KINDS)[number];

/** The four built-in adapters — a CUSTOM runtime name may not collide with
 *  them: built-ins are minted from the dropdown (purpose='binary') and a custom
 *  name is minted from the form (purpose='profile'); the registry rejects a
 *  custom profile that would shadow a built-in. */
const BUILTINS = new Set<string>(KINDS);
/** Mirrors a sane executor-profile identifier: lowercase, starts alpha. */
const NAME_RE = /^[a-z][a-z0-9-]{1,39}$/;

/** The three conformance checks BOTH flows drive — verbatim step ids from
 *  registration_token.DEFAULT_CONFORMANCE_STEPS. Shown as the sequence the CLI
 *  performs, NOT as live per-step status (prereqs can't report it). */
const CONFORMANCE_STEPS: { id: string; label: string }[] = [
  { id: 'workspace_access', label: 'Reads its workspace & skills' },
  { id: 'loopback_reachable', label: 'Reaches HappyRanch at 127.0.0.1' },
  { id: 'cli_callback', label: 'Reports in & registers' },
];

/** Which flow produced the connection — drives the connected-card copy. */
type ConnectMode = 'builtin' | 'custom';
/** A completed connection: display name + resolved path + originating flow. */
interface Connected {
  name: string;
  path: string | null;
  via: ConnectMode;
}

/** Shared field styling — mirrors the Input primitive so the native <select>
 *  matches the design system exactly. */
const FIELD_CLASS =
  'flex h-9 w-full rounded-md border border-border-default bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-accent-default focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50';

/** Build the copy-paste prompt (no `/connect` link). `target` picks the
 *  register route + body while the conformance challenge stays identical:
 *  'binary' → register-binary (built-in path, kind carried by the token),
 *  'profile' → register (custom profile). */
type ConnectTarget = 'binary' | 'profile';

function buildConnectPrompt(
  name: string,
  token: string,
  origin: string,
  target: ConnectTarget,
): string {
  const base = `${origin}/api/v1`;
  const intro =
    target === 'binary'
      ? [
          `# You're connecting the built-in "${name}" CLI to HappyRanch so it`,
          `# can be launched from this machine. Do all of this in one run, then`,
          `# stop. Send this header on every request:`,
          `#   Authorization: Bearer ${token}`,
          ``,
          `# 1. Find your OWN absolute binary path — e.g. the output of`,
          `#      command -v ${name}   (or)   which ${name}`,
        ]
      : [
          `# You're being connected to HappyRanch as an executor named "${name}".`,
          `# Do all of this in one run, then stop. Send this header on every request:`,
          `#   Authorization: Bearer ${token}`,
          ``,
          `# 1. Introduce yourself: work out the exact command that runs you`,
          `#    headless / single-shot, using these placeholders:`,
          `#      {prompt}  {timeout_seconds}  {workspace}`,
        ];
  const registerStep =
    target === 'binary'
      ? [
          `# 3. Register your binary path — POST to`,
          `#    ${base}/executors/runtime/register-binary`,
          `#    body {"path":"<your absolute binary path>"}`,
          `#    (the CLI kind is carried by the token — do NOT send it in the body)`,
        ]
      : [
          `# 3. Register — POST to`,
          `#    ${base}/executors/runtime/register`,
          `#    body {"command":"<your-cli>","argv_template":[...,"{prompt}",...],"adapter":"pi"}`,
        ];
  return [
    ...intro,
    ``,
    `# 2. Complete the conformance challenge — POST each step id to`,
    `#    ${base}/executors/runtime/conformance-checkin`,
    `#    body {"step_id":"<id>"} for each of:`,
    `#      workspace_access   loopback_reachable   cli_callback`,
    ``,
    ...registerStep,
    ``,
    `# This token is valid for about 10 minutes. This screen updates live.`,
  ].join('\n');
}

/** Shared mint → copy-paste → live-poll state machine for BOTH flows. Mints a
 *  scoped runtime registration token (built-in adds purpose='binary'), then
 *  polls GET /health/prereqs until the name is registered. `requirePresent`
 *  gates the match on `p.present`: built-in registration flips `present` true,
 *  so it must be required; a custom profile writes no binary (present stays
 *  false) and instead only APPEARS in prereqs once registered, so custom leaves
 *  it false and matches on appearance. */
function useRuntimeConnect({
  purpose,
  requirePresent,
  via,
  onConnected,
}: {
  purpose?: 'binary';
  requirePresent: boolean;
  via: ConnectMode;
  onConnected: (c: Connected) => void;
}) {
  const [state, setState] = useState<'form' | 'waiting'>('form');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [expiresAt, setExpiresAt] = useState(0); // epoch seconds
  const [expired, setExpired] = useState(false);

  const mint = useMutation({
    mutationFn: (n: string) =>
      settingsApi.mintRuntimeRegistrationToken(
        purpose ? { name: n, purpose } : { name: n },
      ),
    onSuccess: (resp, n) => {
      setName(n);
      setToken(resp.token);
      setExpiresAt(resp.expires_at);
      setExpired(false);
      setState('waiting');
    },
  });

  // Time-based expiry (the mint's only lapse signal — expires_at; there is no
  // conformance-status GET to poll for lapse).
  useEffect(() => {
    if (state !== 'waiting' || !expiresAt) return;
    const ms = expiresAt * 1000 - Date.now();
    if (ms <= 0) {
      setExpired(true);
      return;
    }
    const t = window.setTimeout(() => setExpired(true), ms);
    return () => window.clearTimeout(t);
  }, [state, expiresAt]);

  // Poll the EXISTING prereqs route while waiting; flip to connected the moment
  // the freshly-registered name is registered (present-gated for built-ins,
  // appearance for custom profiles).
  const poll = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    enabled: state === 'waiting' && !expired,
    refetchInterval: state === 'waiting' && !expired ? 2500 : false,
  });

  useEffect(() => {
    if (state !== 'waiting') return;
    const hit = poll.data?.prereqs.find(
      (p) => p.tool === name && (!requirePresent || p.present),
    );
    if (hit) onConnected({ name, path: hit.path, via });
  }, [poll.data, state, name, requirePresent, via, onConnected]);

  const start = (n: string): void => {
    if (n && !mint.isPending) mint.mutate(n);
  };
  const regenerate = (): void => {
    if (name && !mint.isPending) mint.mutate(name);
  };
  const back = (): void => {
    setState('form');
    setToken('');
    setExpiresAt(0);
    setExpired(false);
    mint.reset();
  };

  return { state, name, token, expired, mint, start, regenerate, back };
}

export function ConnectRuntimeStep({
  onContinue,
  onSkip,
}: {
  onContinue: () => void;
  onSkip: () => void;
}): JSX.Element {
  const [mode, setMode] = useState<ConnectMode>('builtin');
  const [connected, setConnected] = useState<Connected | null>(null);

  if (connected) {
    return (
      <ConnectedCard
        connected={connected}
        onContinue={onContinue}
        onReset={() => setConnected(null)}
      />
    );
  }

  return (
    <section className="pt-6 sm:pt-10">
      <StepEyebrow />
      <h1 className="font-display text-display text-text-primary mt-3 font-medium">
        Connect your agentic CLI.
      </h1>

      {mode === 'builtin' ? (
        <BuiltinConnect
          onConnected={setConnected}
          onSkip={onSkip}
          onUseCustom={() => setMode('custom')}
        />
      ) : (
        <CustomConnect
          onConnected={setConnected}
          onSkip={onSkip}
          onUseBuiltin={() => setMode('builtin')}
        />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  BUILT-IN — dropdown → mint binary token → copy prompt → poll       */
/* ------------------------------------------------------------------ */

function BuiltinConnect({
  onConnected,
  onSkip,
  onUseCustom,
}: {
  onConnected: (c: Connected) => void;
  onSkip: () => void;
  onUseCustom: () => void;
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
        onSkip={onSkip}
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
        <button
          type="button"
          onClick={onSkip}
          className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
        >
          Skip — I&rsquo;ll connect a CLI later
        </button>
      </div>

      <HowThisWorks />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  CUSTOM — name → mint profile token → copy prompt → poll for name   */
/* ------------------------------------------------------------------ */

function CustomConnect({
  onConnected,
  onSkip,
  onUseBuiltin,
}: {
  onConnected: (c: Connected) => void;
  onSkip: () => void;
  onUseBuiltin: () => void;
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
        onSkip={onSkip}
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
          <button
            type="button"
            onClick={onUseBuiltin}
            className="text-text-secondary hover:text-text-primary inline-flex items-center gap-1.5 text-xs underline-offset-2 hover:underline"
          >
            <ArrowLeft aria-hidden="true" size={14} />
            Connect a built-in CLI instead
          </button>
          <button
            type="button"
            onClick={onSkip}
            className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
          >
            Skip — I&rsquo;ll connect a CLI later
          </button>
        </div>
        <HowThisWorks />
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Shared waiting body — prompt block + copy + live detect strip      */
/* ------------------------------------------------------------------ */

function WaitingBody({
  name,
  prompt,
  expired,
  regenerating,
  onRegenerate,
  onBack,
  onSkip,
}: {
  name: string;
  prompt: string;
  expired: boolean;
  regenerating: boolean;
  onRegenerate: () => void;
  onBack: () => void;
  onSkip: () => void;
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
        <button
          type="button"
          onClick={onSkip}
          className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
        >
          Skip for now
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Connected — name (FE-known) + registered path (register-real)      */
/* ------------------------------------------------------------------ */

function ConnectedCard({
  connected,
  onContinue,
  onReset,
}: {
  connected: Connected;
  onContinue: () => void;
  onReset: () => void;
}): JSX.Element {
  const subtitle =
    connected.via === 'builtin'
      ? 'Its binary path is registered on this machine — HappyRanch can launch it now. You can manage your CLIs anytime from Settings.'
      : 'Your custom CLI is registered and available to every org. You can manage your CLIs anytime from Settings.';

  return (
    <section className="pt-6 sm:pt-10">
      <StepEyebrow />
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
        {subtitle}
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
        <Button onClick={onContinue}>
          Continue
          <ArrowRight aria-hidden="true" />
        </Button>
        <Button variant="outline" onClick={onReset}>
          Connect another
        </Button>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Small shared bits                                                  */
/* ------------------------------------------------------------------ */

function StepEyebrow(): JSX.Element {
  return (
    <p className="text-accent-text text-xs font-semibold tracking-wider uppercase">
      Step 1 of 2 · Connect your agentic CLI
    </p>
  );
}

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
