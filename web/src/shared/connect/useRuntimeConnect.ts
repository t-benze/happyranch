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
 *  'profile' → register (legacy custom profile via generic-cli),
 *  'adapter' → adapter-submission (v1 wrapper → PENDING → approval → bind). */
export type ConnectTarget = 'binary' | 'profile' | 'adapter';

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
          `#    body {"command":"<your-cli>","argv_template":["<your-cli>","--flag","{prompt}"],"adapter":"pi"}`,
          `#    Note: 'command' is the declared executable; argv_template[0]`,
          `#    must be the SAME executable (the one GenericCliExecutor launches).`,
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
 *  gates the match on `p.present`: built-in registration flips `present` true
 *  (executors.json entry), so it must be required; a custom profile's
 *  `present`/`path` derives from its declared command's PATH resolvability
 *  (same contract as /health/prereqs), so a custom profile WITH a resolvable
 *  command can also match on present. Leave `requirePresent` false for custom
 *  to match on appearance alone. */
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

/** Build the adapter-backed connect prompt (THR-107 seq141). The prompt
 *  directs the candidate CLI to create a v1 AdapterInput/AdapterOutput wrapper
 *  executable, run conformance check-ins, and submit it via
 *  POST /runtime/adapters/submit with the scoped adapter-purpose token.
 *  The adapter becomes PENDING; this screen updates live. */
export function buildAdapterConnectPrompt(
  name: string,
  token: string,
  origin: string,
): string {
  const base = `${origin}/api/v1`;
  return [
    `# Connect "${name}" to HappyRanch as a custom-adapter-backed CLI.`,
    `# Do all of this in one run, then stop. Send this header on every request:`,
    `#   Authorization: Bearer ${token}`,
    ``,
    `# 1. Create a v1 adapter wrapper executable that reads AdapterInput JSON`,
    `#    from stdin and writes AdapterOutput JSON to stdout. It must:`,
    `#    - Accept a v1 AdapterInput JSON object on stdin`,
    `#    - Invoke your CLI with the prompt, workspace, and timeout from the input`,
    `#    - Collect your CLI's output and wrap it in a v1 AdapterOutput JSON object`,
    `#    - Write exactly one AdapterOutput JSON object to stdout, then exit`,
    `#    The AdapterInput/AdapterOutput contract is defined in the runtime:`,
    `#    runtime/orchestrator/adapter_contract.py`,
    ``,
    `# 2. Complete the conformance challenge — POST each step id to`,
    `#    ${base}/executors/runtime/conformance-checkin`,
    `#    body {"step_id":"<id>"} for each of:`,
    `#      workspace_access   loopback_reachable   cli_callback`,
    `#    then post emit_envelope with your adapter's sample output.`,
    ``,
    `# 3. Submit your adapter — POST to`,
    `#    ${base}/runtime/adapters/submit`,
    `#    body {"executable":"<absolute-path-to-wrapper>","version":"1.0.0",`,
    `#         "capabilities":["token_metering"],"workspace_adapter":"pi"}`,
    ``,
    `# After submission, the adapter is PENDING founder approval.`,
    `# Once approved, it will be bound to the "${name}" profile automatically.`,
    ``,
    `# This token is valid for about 10 minutes. This screen updates live.`,
  ].join('\n');
}

/** Adapter-backed connection status matching the adapter lifecycle. */
export type AdapterState =
  | { stage: 'form' }
  | { stage: 'waiting'; name: string; token: string; expired: boolean; adapterId: string }
  | { stage: 'submitted'; name: string; adapterId: string; status: string }
  | { stage: 'bind_failed'; name: string; adapterId: string; error: string }
  | { stage: 'connected'; name: string; adapterId: string };

/** Shared hook for the adapter-backed custom-CLI connection (THR-107 seq141).
 *  Mints an adapter-purpose token → CLI creates/submits v1 adapter wrapper
 *  → UI polls adapter status → binds profile when APPROVED. */
export function useAdapterConnect({
  onConnected,
}: {
  onConnected: (c: Connected) => void;
}) {
  const [state, setState] = useState<AdapterState>({ stage: 'form' });
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [expiresAt, setExpiresAt] = useState(0);

  const mint = useMutation({
    mutationFn: (n: string) =>
      settingsApi.mintRuntimeRegistrationToken({
        name: n,
        purpose: 'adapter',
        intended_profile_name: n,
      }),
    onSuccess: (resp, n) => {
      const aid = `${n}-adapter`;
      setName(n);
      setToken(resp.token);
      setExpiresAt(resp.expires_at);
      setState({ stage: 'waiting', name: n, token: resp.token, expired: false, adapterId: aid });
    },
  });

  // Time-based expiry
  useEffect(() => {
    if (state.stage !== 'waiting' || state.expired || !expiresAt) return;
    const ms = expiresAt * 1000 - Date.now();
    if (ms <= 0) {
      setState((s) =>
        s.stage === 'waiting' ? { ...s, expired: true } : s,
      );
      return;
    }
    const t = window.setTimeout(() => {
      setState((s) =>
        s.stage === 'waiting' ? { ...s, expired: true } : s,
      );
    }, ms);
    return () => window.clearTimeout(t);
  }, [state.stage, expiresAt]);

  // Poll the adapter endpoint for status changes
  const adapterIdForPoll = 'adapterId' in state ? (state as { adapterId: string }).adapterId : '';
  const pollEnabled =
    (state.stage === 'waiting' && !('expired' in state ? state.expired : false)) ||
    state.stage === 'submitted';

  const { data: adapterEntry } = useQuery({
    queryKey: ['adapter', adapterIdForPoll],
    queryFn: () => import('@/lib/api').then(({ adapters }) => adapters.getAdapter(adapterIdForPoll)),
    enabled: pollEnabled && adapterIdForPoll !== '',
    refetchInterval: pollEnabled && adapterIdForPoll !== '' ? 2500 : false,
  });

  // Transition: waiting → submitted when adapter appears as PENDING
  useEffect(() => {
    if (state.stage !== 'waiting' || 'expired' in state && state.expired) return;
    if (adapterEntry && adapterEntry.status === 'pending') {
      setState({
        stage: 'submitted',
        name,
        adapterId: adapterIdForPoll,
        status: adapterEntry.status,
      });
    }
  }, [adapterEntry, state.stage, name, adapterIdForPoll]);

  // Transition: submitted → connected when adapter is APPROVED → bind profile
  const bindMutation = useMutation({
    mutationFn: (aid: string) =>
      import('@/lib/api').then(({ adapters }) =>
        adapters.bindAdapterProfile(aid, { profile_name: name }),
      ),
    onSuccess: () => {
      setState({ stage: 'connected', name, adapterId: adapterIdForPoll });
      onConnected({ name, path: null, via: 'custom' });
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error ? error.message : 'Bind failed — retry or contact the founder.';
      setState({ stage: 'bind_failed', name, adapterId: adapterIdForPoll, error: message });
    },
  });

  const retryBind = (): void => {
    if (!bindMutation.isPending) bindMutation.mutate(adapterIdForPoll);
  };

  useEffect(() => {
    if (state.stage !== 'submitted') return;
    if (adapterEntry && adapterEntry.status === 'approved') {
      // Auto-bind the approved adapter to the profile
      if (!bindMutation.isPending && !bindMutation.isSuccess) {
        bindMutation.mutate(adapterIdForPoll);
      }
    }
  }, [adapterEntry, state.stage, adapterIdForPoll, bindMutation]);

  const start = (n: string): void => {
    if (n && !mint.isPending) mint.mutate(n);
  };
  const regenerate = (): void => {
    if (name && !mint.isPending) mint.mutate(name);
  };
  const back = (): void => {
    setState({ stage: 'form' });
    setToken('');
    setExpiresAt(0);
    mint.reset();
  };

  return { state, name, token, adapterId: adapterIdForPoll, mint, start, regenerate, back, bindMutation, retryBind };
}
