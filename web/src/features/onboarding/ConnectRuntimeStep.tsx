/**
 * ConnectRuntimeStep — THR-088 onboarding Step 1 of 2: "Connect your agentic
 * CLI". Two coexisting flows behind one step:
 *
 *   PRIMARY — built-in dropdown (THR-088 msg22 fix). The user picks a built-in
 *   agentic CLI (claude / codex / opencode / pi from EXECUTOR_BINARY_KINDS).
 *   Registering a built-in = recording its BINARY PATH in the machine-local
 *   executor-binaries registry:
 *     - GET  /health/prereqs RESOLVES each present built-in's absolute path, so
 *       a detected CLI PRE-FILLS the path and the user just confirms it.
 *     - POST /executor-binaries/register {kind, path} validates + stores it.
 *     - POST /executor-binaries/validate {path} gives inline feedback when the
 *       CLI isn't on PATH and the user types an absolute path by hand.
 *   The CONNECT SIGNAL for a built-in is the SYNCHRONOUS register success
 *   (valid:true) — NOT the prereqs "name appears" poll the custom flow uses.
 *   Built-ins are ALWAYS in prereqs, so that poll would false-positive instantly.
 *
 *   SECONDARY — custom CLI (unchanged). A copy-paste prompt the user pastes into
 *   their own conformant CLI, which self-drives the EXISTING loopback
 *   registration routes. There is deliberately NO `/connect` one-click route
 *   (founder ruling). "detecting → connected" is driven by POLLING the EXISTING
 *   GET /health/prereqs until the freshly-registered custom NAME appears.
 *
 * Registration-only (founder ruling THR-085 msg45): prereqs is READ to pre-fill
 * a detected path; there is NO detect/scan route and none is added here.
 *
 * Honesty fence (THR-061 §D; THR-088): Pasture tokens only; no invented status.
 * The built-in connected card shows the kind (FE-known) + the registered PATH
 * (register-real). The custom flow's 3 conformance checks render as the
 * described SEQUENCE, not live-ticking badges (prereqs can't report per-step
 * progress). No `version`, no `argv_template` — those fields don't exist here.
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
 *  them (register 422s on builtin collision), and a builtin already present in
 *  prereqs would otherwise false-positive the custom detect poll. */
const BUILTINS = new Set<string>(KINDS);
/** Mirrors a sane executor-profile identifier: lowercase, starts alpha. */
const NAME_RE = /^[a-z][a-z0-9-]{1,39}$/;

/** The three conformance checks the CUSTOM CLI drives — verbatim step ids from
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

/** Extract a human-readable message from an ApiError (422 detail is a string)
 *  or any thrown value. */
function errMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (typeof err.detail === 'string') return err.detail;
    if (err.detail && typeof err.detail === 'object' && 'msg' in err.detail) {
      return String((err.detail as { msg: unknown }).msg);
    }
    return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

/** Build the copy-paste prompt for the custom flow (no `/connect` link). */
function buildConnectPrompt(name: string, token: string, origin: string): string {
  const base = `${origin}/api/v1`;
  return [
    `# You're being connected to HappyRanch as an executor named "${name}".`,
    `# Do all of this in one run, then stop. Send this header on every request:`,
    `#   Authorization: Bearer ${token}`,
    ``,
    `# 1. Introduce yourself: work out the exact command that runs you`,
    `#    headless / single-shot, using these placeholders:`,
    `#      {prompt}  {timeout_seconds}  {workspace}`,
    ``,
    `# 2. Complete the conformance challenge — POST each step id to`,
    `#    ${base}/executors/runtime/conformance-checkin`,
    `#    body {"step_id":"<id>"} for each of:`,
    `#      workspace_access   loopback_reachable   cli_callback`,
    ``,
    `# 3. Register — POST to`,
    `#    ${base}/executors/runtime/register`,
    `#    body {"command":"<your-cli>","argv_template":[...,"{prompt}",...],"adapter":"pi"}`,
    ``,
    `# This token is valid for about 10 minutes. This screen updates live.`,
  ].join('\n');
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
/*  PRIMARY — built-in dropdown → detect/confirm OR manual path        */
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
  const [pathInput, setPathInput] = useState('');
  const [check, setCheck] = useState<{ valid: boolean; error: string | null } | null>(null);
  const [registerError, setRegisterError] = useState<string | null>(null);

  // READ prereqs to pre-fill a detected built-in's resolved path. Registration
  // stays the connect signal — this query never drives "connected".
  const prereqs = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    staleTime: 120_000,
    retry: 1,
  });

  const validate = useMutation({
    mutationFn: (path: string) => executorBinaries.validateExecutorBinary({ path }),
  });
  const register = useMutation({
    mutationFn: (body: { kind: string; path: string }) =>
      executorBinaries.registerExecutorBinary(body),
  });

  const detected = kind ? prereqs.data?.prereqs.find((p) => p.tool === kind) : undefined;
  const detectedPath = detected?.present ? (detected.path ?? null) : null;
  const detectionReady = !prereqs.isPending;
  const busy = register.isPending || validate.isPending;
  const trimmedPath = pathInput.trim();

  const onSelect = (value: string): void => {
    setKind(value as Kind | '');
    setPathInput('');
    setCheck(null);
    setRegisterError(null);
    validate.reset();
  };

  // The ONLY connect signal: a synchronous register that returns valid:true.
  const doRegister = async (path: string): Promise<void> => {
    if (!kind) return;
    setRegisterError(null);
    try {
      const resp = await register.mutateAsync({ kind, path });
      if (resp.valid) {
        onConnected({ name: resp.kind, path: resp.path, via: 'builtin' });
      } else {
        setRegisterError(
          'The daemon stored the path but could not run it. Double-check the binary and try again.',
        );
      }
    } catch (err) {
      setRegisterError(errMessage(err, 'Could not register this path.'));
    }
  };

  const onValidate = async (): Promise<void> => {
    setRegisterError(null);
    setCheck(null);
    try {
      const res = await validate.mutateAsync(trimmedPath);
      setCheck({ valid: res.valid, error: res.error });
    } catch (err) {
      setCheck({ valid: false, error: errMessage(err, 'Validation failed.') });
    }
  };

  return (
    <div className="mt-6 max-w-lg">
      <p className="text-text-secondary text-base leading-relaxed">
        Pick the agentic CLI you run — Claude Code, Codex, opencode, or Pi.
        HappyRanch records where its binary lives on this machine so it can
        launch it.
      </p>

      <div className="mt-6 space-y-2">
        <Label htmlFor="builtin-kind">Pick your agentic CLI</Label>
        <select
          id="builtin-kind"
          value={kind}
          onChange={(e) => onSelect(e.target.value)}
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

      {kind && (
        <div className="mt-4">
          {!detectionReady ? (
            <p className="text-text-muted flex items-center gap-2 text-sm">
              <Spinner className="text-text-muted h-4 w-4" />
              Checking this machine for <span className="font-mono">{kind}</span>…
            </p>
          ) : detectedPath ? (
            /* (a) present in prereqs → confirm the resolved path */
            <div className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-4">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="bg-feedback-success/15 text-feedback-success inline-flex h-6 w-6 items-center justify-center rounded-full"
                >
                  <Check size={14} />
                </span>
                <p className="text-text-primary text-sm font-medium">
                  Found <span className="font-mono">{kind}</span> on this machine
                </p>
              </div>
              <p className="text-text-muted text-caption mt-3 font-semibold tracking-wider uppercase">
                Detected at
              </p>
              <p className="text-text-secondary mt-1 truncate font-mono text-xs">
                {detectedPath}
              </p>
              {registerError && (
                <p className="text-feedback-danger mt-3 text-sm" role="alert">
                  {registerError}
                </p>
              )}
              <div className="mt-4">
                <Button
                  type="button"
                  onClick={() => void doRegister(detectedPath)}
                  disabled={busy}
                >
                  {register.isPending ? 'Connecting…' : 'Confirm & connect'}
                </Button>
              </div>
            </div>
          ) : (
            /* (b) not present → manual absolute-path entry */
            <div className="border-border-default bg-surface shadow-pasture-sm rounded-lg border p-4">
              <p className="text-text-secondary text-sm leading-relaxed">
                <span className="font-mono">{kind}</span> isn&rsquo;t on this
                machine&rsquo;s PATH. Enter the absolute path to its binary — for
                example, the output of{' '}
                <span className="text-text-primary font-mono">which {kind}</span>.
              </p>
              <div className="mt-3 space-y-2">
                <Label htmlFor="builtin-path">Binary path</Label>
                <Input
                  id="builtin-path"
                  value={pathInput}
                  onChange={(e) => {
                    setPathInput(e.target.value);
                    setCheck(null);
                    setRegisterError(null);
                  }}
                  placeholder={`/absolute/path/to/${kind}`}
                  className="font-mono"
                  autoComplete="off"
                  spellCheck={false}
                />
                {check && (
                  <p
                    className={`flex items-center gap-1.5 text-sm ${
                      check.valid ? 'text-feedback-success' : 'text-feedback-danger'
                    }`}
                    role="status"
                  >
                    {check.valid && <Check aria-hidden="true" size={13} />}
                    {check.valid
                      ? 'Looks good — this path is absolute, exists, and is executable.'
                      : (check.error ?? 'This path is not valid.')}
                  </p>
                )}
                {registerError && (
                  <p className="text-feedback-danger text-sm" role="alert">
                    {registerError}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <Button
                    type="button"
                    onClick={() => void doRegister(trimmedPath)}
                    disabled={!trimmedPath || busy}
                  >
                    {register.isPending ? 'Connecting…' : 'Register & connect'}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void onValidate()}
                    disabled={!trimmedPath || busy}
                  >
                    {validate.isPending ? 'Validating…' : 'Validate'}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-4">
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

      <HowThisWorksBuiltin />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SECONDARY — custom CLI: mint token → copy prompt → poll for name   */
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
  const [state, setState] = useState<'form' | 'waiting'>('form');
  const [nameInput, setNameInput] = useState('');
  const [runtimeName, setRuntimeName] = useState(''); // locked name post-generate
  const [token, setToken] = useState('');
  const [expiresAt, setExpiresAt] = useState(0); // epoch seconds
  const [expired, setExpired] = useState(false);

  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const nameIsBuiltin = BUILTINS.has(nameInput.trim());
  const nameValid = NAME_RE.test(nameInput.trim()) && !nameIsBuiltin;

  const mint = useMutation({
    mutationFn: (name: string) => settingsApi.mintRuntimeRegistrationToken({ name }),
    onSuccess: (resp, name) => {
      setRuntimeName(name);
      setToken(resp.token);
      setExpiresAt(resp.expires_at);
      setExpired(false);
      setState('waiting');
    },
  });

  // Time-based expiry (the only expiry signal the custom shape has — the mint
  // returns expires_at; there is no conformance-status GET to poll for lapse).
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
  // the freshly-registered custom NAME appears. A custom profile only shows up
  // post-register, so its appearance IS the connect signal for THIS flow.
  const poll = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    enabled: state === 'waiting' && !expired,
    refetchInterval: state === 'waiting' && !expired ? 2500 : false,
  });

  useEffect(() => {
    if (state !== 'waiting') return;
    const match = poll.data?.prereqs.find((p) => p.tool === runtimeName);
    if (match) onConnected({ name: runtimeName, path: match.path, via: 'custom' });
  }, [poll.data, state, runtimeName, onConnected]);

  const generate = (): void => {
    const name = nameInput.trim();
    if (nameValid && !mint.isPending) mint.mutate(name);
  };

  const regenerate = (): void => {
    if (runtimeName && !mint.isPending) mint.mutate(runtimeName);
  };

  const back = (): void => {
    setState('form');
    setToken('');
    setExpiresAt(0);
    setExpired(false);
    mint.reset();
  };

  if (state === 'waiting') {
    return (
      <WaitingBody
        name={runtimeName}
        prompt={buildConnectPrompt(runtimeName, token, origin)}
        expired={expired}
        regenerating={mint.isPending}
        onRegenerate={regenerate}
        onBack={back}
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
            mint.reset();
          }}
          placeholder="e.g. my-cli"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-invalid={nameInput && !nameValid && !mint.isPending ? true : undefined}
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
        {mint.isError && (
          <p className="text-feedback-danger text-sm" role="alert">
            {mint.error instanceof ApiError
              ? `Could not generate a prompt (${mint.error.status}).`
              : 'Could not generate a prompt. Is the daemon reachable?'}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3 pt-3">
          <Button type="submit" disabled={!nameValid || mint.isPending}>
            {mint.isPending ? 'Generating…' : 'Generate connect prompt'}
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
/*  Custom waiting body — prompt block + copy + live detect strip      */
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

/** Honesty note for the built-in path-registration flow. */
function HowThisWorksBuiltin(): JSX.Element {
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
        HappyRanch only records where the CLI&rsquo;s binary lives on this
        machine — nothing runs when you connect it. Registering makes the CLI
        available to choose; assigning an agent to run on it is a separate,
        later step.
      </p>
    </details>
  );
}

/** Honesty note for the custom copy-paste flow. */
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
