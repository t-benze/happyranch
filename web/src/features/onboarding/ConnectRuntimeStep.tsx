/**
 * ConnectRuntimeStep — THR-088 onboarding Step 1 of 2: "Connect your agent
 * runtime". The founder-confirmed SIMPLE shape: a copy-paste prompt the user
 * pastes into their own agentic CLI. That CLI self-drives the EXISTING
 * loopback registration routes; there is deliberately NO `/connect` one-click
 * route (founder ruling) and none is built here.
 *
 * Flow (all FE, no new backend route):
 *   1. The user names the runtime, then Generate mints a runtime-level
 *      registration token — POST /auth/registration-token/runtime {name}.
 *      The daemon binds that `name` to the profile the CLI will register
 *      (`profile_name = record.name`), so the FE fixes the name up front.
 *   2. The copy-paste prompt embeds the token + the two loopback routes the
 *      CLI drives itself:
 *        POST /api/v1/executors/runtime/conformance-checkin  (the 3 checks)
 *        POST /api/v1/executors/runtime/register             (the profile)
 *   3. "detecting → connected" is driven by POLLING the EXISTING
 *      GET /health/prereqs (queryKey ['health','prereqs']). A runtime
 *      register calls registry.register_custom_profile, so the new NAME
 *      surfaces in prereqs — we poll until it appears, then flip to connected.
 *
 * Honesty fence (THR-061 §D; THR-088): Pasture tokens only; no invented
 * status. prereqs cannot report per-step conformance progress (that route is
 * POST/CLI-only), so the 3 named checks render as the described SEQUENCE, not
 * live-ticking ok/failed badges — anything else would be fabricated. The
 * connected card shows only the NAME (FE-known) + resolved PATH (prereqs-real);
 * the design's "we'll run it as {launch cmd}" + adapter lines are OMITTED
 * because prereqs returns only {tool, present, path, hint} — no argv_template.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ArrowLeft, ArrowRight, Check, ChevronRight, RefreshCw } from 'lucide-react';
import { ApiError, health as healthApi, settings as settingsApi } from '@/lib/api';
import type { ExecutorPrereq } from '@/lib/api/types';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';

/** The four built-in adapters — a custom runtime name may not collide with
 *  them (register 422s on builtin collision), and a builtin already present
 *  in prereqs would otherwise false-positive the detect poll. */
const BUILTINS = new Set(['claude', 'codex', 'opencode', 'pi']);
/** Mirrors a sane executor-profile identifier: lowercase, starts alpha. */
const NAME_RE = /^[a-z][a-z0-9-]{1,39}$/;

/** The three conformance checks the CLI drives — verbatim step ids from
 *  registration_token.DEFAULT_CONFORMANCE_STEPS. Shown as the sequence the
 *  CLI performs, NOT as live per-step status (prereqs can't report it). */
const CONFORMANCE_STEPS: { id: string; label: string }[] = [
  { id: 'workspace_access', label: 'Reads its workspace & skills' },
  { id: 'loopback_reachable', label: 'Reaches HappyRanch at 127.0.0.1' },
  { id: 'cli_callback', label: 'Reports in & registers' },
];

type ConnectState = 'form' | 'waiting' | 'connected';

/** Build the copy-paste prompt for the SIMPLE shape (no `/connect` link). */
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
  const [state, setState] = useState<ConnectState>('form');
  const [nameInput, setNameInput] = useState('');
  const [runtimeName, setRuntimeName] = useState(''); // locked name post-generate
  const [token, setToken] = useState('');
  const [expiresAt, setExpiresAt] = useState(0); // epoch seconds
  const [expired, setExpired] = useState(false);
  const [connected, setConnected] = useState<ExecutorPrereq | null>(null);

  const origin = typeof window !== 'undefined' ? window.location.origin : '';

  const nameIsBuiltin = BUILTINS.has(nameInput.trim());
  const nameValid = NAME_RE.test(nameInput.trim()) && !nameIsBuiltin;

  const mint = useMutation({
    mutationFn: (name: string) =>
      settingsApi.mintRuntimeRegistrationToken({ name }),
    onSuccess: (resp, name) => {
      setRuntimeName(name);
      setToken(resp.token);
      setExpiresAt(resp.expires_at);
      setExpired(false);
      setState('waiting');
    },
  });

  // Time-based expiry (the only expiry signal the SIMPLE shape has — the mint
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

  // Poll the EXISTING prereqs route while waiting; flip to connected the
  // moment the freshly-registered NAME appears. A custom profile only shows
  // up post-register, so its appearance IS the connect signal.
  const poll = useQuery({
    queryKey: ['health', 'prereqs'],
    queryFn: healthApi.getPrereqs,
    enabled: state === 'waiting' && !expired,
    refetchInterval: state === 'waiting' && !expired ? 2500 : false,
  });

  useEffect(() => {
    if (state !== 'waiting') return;
    const match = poll.data?.prereqs.find((p) => p.tool === runtimeName);
    if (match) {
      setConnected(match);
      setState('connected');
    }
  }, [poll.data, state, runtimeName]);

  const generate = (): void => {
    const name = nameInput.trim();
    if (nameValid && !mint.isPending) mint.mutate(name);
  };

  const regenerate = (): void => {
    if (runtimeName && !mint.isPending) mint.mutate(runtimeName);
  };

  const reset = (): void => {
    setState('form');
    setNameInput('');
    setRuntimeName('');
    setToken('');
    setExpiresAt(0);
    setExpired(false);
    setConnected(null);
    mint.reset();
  };

  /* --------------------------------------------------------------- */
  /*  Connected — name (FE-known) + resolved path (prereqs-real)      */
  /* --------------------------------------------------------------- */
  if (state === 'connected' && connected) {
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
            <span className="font-mono">{connected.tool}</span> connected.
          </h1>
        </div>
        <p className="text-text-secondary mt-3 max-w-lg text-base leading-relaxed">
          Your runtime is registered and available to every org. You can manage
          runtimes anytime from Settings.
        </p>

        <div className="bg-surface border-border-default shadow-pasture-sm mt-6 max-w-lg rounded-lg border p-4">
          <p className="text-text-muted text-caption font-semibold tracking-wider uppercase">
            Name
          </p>
          <p className="text-text-primary mt-1 font-mono text-sm font-medium">
            {connected.tool}
          </p>
          <p className="text-text-muted text-caption mt-3 font-semibold tracking-wider uppercase">
            Found at
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
          <Button variant="outline" onClick={reset}>
            Connect another
          </Button>
        </div>
      </section>
    );
  }

  /* --------------------------------------------------------------- */
  /*  Form + Waiting share the header; the body switches on state.    */
  /* --------------------------------------------------------------- */
  return (
    <section className="pt-6 sm:pt-10">
      <StepEyebrow />
      <h1 className="font-display text-display text-text-primary mt-3 font-medium">
        Connect your agent runtime.
      </h1>
      <p className="text-text-secondary mt-3 max-w-lg text-base leading-relaxed">
        HappyRanch runs work through an agent CLI — Claude Code, Codex, or any
        conformant CLI. Name it, then paste the generated prompt into your CLI
        to connect it. It proves it works and tells us how to launch it.
      </p>

      {state === 'form' ? (
        <form
          className="mt-7 max-w-lg space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            generate();
          }}
        >
          <Label htmlFor="runtime-name">Name this runtime</Label>
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
            placeholder="e.g. claude-code"
            autoFocus
            autoComplete="off"
            spellCheck={false}
            aria-invalid={
              nameInput && !nameValid && !mint.isPending ? true : undefined
            }
          />
          <p className="text-xs">
            {nameInput && nameIsBuiltin ? (
              <span className="text-feedback-danger">
                Pick a name that isn&rsquo;t a built-in (claude, codex, opencode,
                pi).
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
              onClick={onSkip}
              className="text-text-muted hover:text-text-secondary text-xs underline-offset-2 hover:underline"
            >
              Skip — I&rsquo;ll connect a runtime later
            </button>
          </div>
          <HowThisWorks />
        </form>
      ) : (
        <WaitingBody
          name={runtimeName}
          prompt={buildConnectPrompt(runtimeName, token, origin)}
          expired={expired}
          regenerating={mint.isPending}
          onRegenerate={regenerate}
          onBack={reset}
          onSkip={onSkip}
        />
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Waiting body — prompt block + copy + live detect strip            */
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
              connect prompt · paste into your agent CLI
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
/*  Small shared bits                                                  */
/* ------------------------------------------------------------------ */

function StepEyebrow(): JSX.Element {
  return (
    <p className="text-accent-text text-xs font-semibold tracking-wider uppercase">
      Step 1 of 2 · Connect your agent runtime
    </p>
  );
}

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
