/**
 * Shared runtime-connect engine — the mint → copy-paste → live-poll state
 * machine and the copy-paste prompt builder, extracted verbatim from the
 * onboarding ConnectRuntimeStep (THR-088) so BOTH onboarding and Settings ▸
 * Executors consume ONE implementation with no logic/contract fork (THR-107).
 *
 * The binary-vs-profile split is a runtime PARAMETER of this engine, not two
 * code paths: built-in mints a purpose='binary' token and targets
 * register-binary (poll requires `present`); custom mints a profile token and
 * targets register (poll matches on appearance). See ConnectRuntimeStep's
 * header comment for the full honesty-fence rationale (THR-061 §D; THR-088).
 *
 * This module is CHROME-FREE: no step eyebrow, no wizard headings, no
 * Continue/Skip navigation. Consumers inject that chrome via ConnectFlow slots.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { executorBinaries, health as healthApi, settings as settingsApi } from '@/lib/api';

/** The built-in executor kinds, derived from the api client's canonical list. */
export const KINDS = executorBinaries.EXECUTOR_BINARY_KINDS;
export type Kind = (typeof KINDS)[number];

/** The four built-in adapters — a CUSTOM runtime name may not collide with
 *  them: built-ins are minted from the dropdown (purpose='binary') and a custom
 *  name is minted from the form (purpose='profile'); the registry rejects a
 *  custom profile that would shadow a built-in. */
export const BUILTINS = new Set<string>(KINDS);
/** Mirrors a sane executor-profile identifier: lowercase, starts alpha. */
export const NAME_RE = /^[a-z][a-z0-9-]{1,39}$/;

/** The four conformance checks BOTH flows drive — verbatim step ids from
 *  registration_token.DEFAULT_CONFORMANCE_STEPS. Shown as the sequence the CLI
 *  performs, NOT as live per-step status (prereqs can't report it). */
export const CONFORMANCE_STEPS: { id: string; label: string }[] = [
  { id: 'workspace_access', label: 'Reads its workspace & skills' },
  { id: 'loopback_reachable', label: 'Reaches HappyRanch at 127.0.0.1' },
  { id: 'cli_callback', label: 'Reports in & registers' },
  { id: 'emit_envelope', label: 'Produces a valid result-envelope' },
];

/** Which flow produced the connection — drives the connected-card copy. */
export type ConnectMode = 'builtin' | 'custom';
/** A completed connection: display name + resolved path + originating flow. */
export interface Connected {
  name: string;
  path: string | null;
  via: ConnectMode;
}

/** Shared field styling — mirrors the Input primitive so the native <select>
 *  matches the design system exactly. */
export const FIELD_CLASS =
  'flex h-9 w-full rounded-md border border-border-default bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-accent-default focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50';

/** Build the copy-paste prompt (no `/connect` link). `target` picks the
 *  register route + body while the conformance challenge stays identical:
 *  'binary' → register-binary (built-in path, kind carried by the token),
 *  'profile' → register (custom profile). */
export type ConnectTarget = 'binary' | 'profile';

export function buildConnectPrompt(
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
    `#    then post emit_envelope with a sample envelope:`,
    `#    body {"step_id":"emit_envelope","envelope":{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1,"model":"custom-cli"}}}`,
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
export function useRuntimeConnect({
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
