/**
 * Shared runtime-connect engine — the mint → copy-paste → live-poll state
 * machine and the copy-paste prompt builder, extracted verbatim from the
 * onboarding ConnectRuntimeStep (THR-088) so BOTH onboarding and Settings ▸
 * Executors consume ONE implementation with no logic/contract fork (THR-107).
 *
 * The binary-vs-profile split is a runtime PARAMETER of this engine, not two
 * code paths: built-in mints a purpose='binary' token and targets
 * register-binary (poll requires `present`); custom mints a profile-purpose
 * token first, then the consumer handles a separate binary-purpose stage
 * (see ConnectFlow's two-stage custom flow: ProfileStage → BinaryStage).
 * ProfileStage passes `requirePresent: false` — appearance-only is deliberate
 * to permit advancing to the binary stage; only BinaryStage
 * (`requirePresent: true`) may report the externally connected state.
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
 *  'adapter' → adapter-submission (v1 wrapper → PENDING → founder approves & connects atomically, seq237). */
export type ConnectTarget = 'binary' | 'profile' | 'adapter';

/** Escape a string for safe single-quoted shell use (single quotes -> '\''). */
function shQuote(s: string): string {
  return `'${s.replace(/'/g, "'\\''")}'`;
}

export function buildConnectPrompt(
  name: string,
  token: string,
  origin: string,
  target: ConnectTarget,
): string {
  const base = `${origin}/api/v1`;
  if (target === 'binary') {
    // THR-107 seq352: strictly sequential, copy-pasteable, failure-visible.
    // Every curl uses --fail-with-body -sS so HTTP errors abort the script
    // AND the server error detail is still printed for the operator.
    const TOKEN = shQuote(token);
    const BASE = shQuote(base);
    const BIN_NAME = shQuote(name);
    return [
      `# Connect the built-in ${BIN_NAME} CLI to HappyRanch.`,
      `# Copy this whole block and run it — each command runs sequentially.`,
      `# The script stops immediately if any curl returns an HTTP error.`,
      ``,
      `TOKEN=${TOKEN}`,
      `BASE=${BASE}`,
      ``,
      `# 1. Discover your own absolute binary path`,
      `BIN=$(command -v ${BIN_NAME} 2>/dev/null || which ${BIN_NAME} 2>/dev/null)`,
      `if [ -z "$BIN" ]; then`,
      `  echo "ERROR: cannot find executable ${name} on PATH — install it first" >&2`,
      `  exit 1`,
      `fi`,
      `if [ ! -x "$BIN" ]; then`,
      `  echo "ERROR: $BIN exists but is not executable" >&2`,
      `  exit 1`,
      `fi`,
      `echo "Found binary: $BIN"`,
      ``,
      `# 2. Conformance check-ins — POST each step id in order`,
      `echo "--- workspace_access ---"`,
      `curl --fail-with-body -sS -X POST "$BASE/executors/runtime/conformance-checkin" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"step_id":"workspace_access"}'`,
      `echo ""`,
      ``,
      `echo "--- loopback_reachable ---"`,
      `curl --fail-with-body -sS -X POST "$BASE/executors/runtime/conformance-checkin" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"step_id":"loopback_reachable"}'`,
      `echo ""`,
      ``,
      `echo "--- cli_callback ---"`,
      `curl --fail-with-body -sS -X POST "$BASE/executors/runtime/conformance-checkin" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"step_id":"cli_callback"}'`,
      `echo ""`,
      ``,
      `echo "--- emit_envelope (fourth check-in) ---"`,
      `RESP=$(curl --fail-with-body -sS -X POST "$BASE/executors/runtime/conformance-checkin" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"step_id":"emit_envelope","envelope":{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1,"model":"custom-cli"}}}')`,
      `echo "$RESP"`,
      ``,
      `# 3. Gate: the fourth response MUST report all_complete:true`,
      `if ! echo "$RESP" | grep -q '"all_complete":true'; then`,
      `  echo "ERROR: conformance is not complete — see the emit_envelope response above" >&2`,
      `  echo "Each step must be completed sequentially. Check that every curl returned" >&2`,
      `  echo "a 200 response with arrived:true before retrying." >&2`,
      `  exit 1`,
      `fi`,
      ``,
      `# 4. Register the binary path (the kind is carried by the token)`,
      `echo "--- register-binary ---"`,
      `curl --fail-with-body -sS -X POST "$BASE/executors/runtime/register-binary" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d "{\\"path\\":\\"$BIN\\"}"`,
      `echo ""`,
    ].join('\n');
  }

  // Profile and adapter targets — unchanged semantic behavior (THR-107 seq352).
  const intro =
    target === 'profile'
      ? [
          `# You're being connected to HappyRanch as an executor named "${name}".`,
          `# Do all of this in one run, then stop. Send this header on every request:`,
          `#   Authorization: Bearer ${token}`,
          ``,
          `# 1. Introduce yourself: work out the exact command that runs you`,
          `#    headless / single-shot, using these placeholders:`,
          `#      {prompt}  {timeout_seconds}  {workspace}`,
        ]
      : [];
  const registerStep =
    target === 'profile'
      ? [
          `# 3. Register — POST to`,
          `#    ${base}/executors/runtime/register`,
          `#    body {"command":"<your-cli>","argv_template":["<your-cli>","--flag","{prompt}"],"adapter":"pi"}`,
          `#    Note: 'command' is the declared executable; argv_template[0]`,
          `#    must be the SAME executable (the one GenericCliExecutor launches).`,
        ]
      : [];
  return [
    ...intro,
    ``,
    `# 2. Complete the conformance challenge — POST each step id to`,
    `#    ${base}/executors/runtime/conformance-checkin`,
    `#    body {"step_id":"<id>"} for each of:`,
    `#      workspace_access   loopback_reachable   cli_callback`,
    `#    then post emit_envelope with a sample envelope:`,
    `#    body {"step_id":"emit_envelope","envelope":{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1}}}`,
    ``,
    ...registerStep,
    ``,
    `# This token is valid for about 30 minutes. This screen updates live.`,
  ].join('\n');
}

/** Shared mint → copy-paste → live-poll state machine for BOTH flows. Mints a
 *  scoped runtime registration token (built-in adds purpose='binary'), then
 *  polls GET /health/prereqs until the name is registered. `requirePresent`
 *  gates the match on `p.present`: both built-in and custom profiles derive
 *  `present`/`path` from the machine-local binary registry (executors.json)
 *  keyed by the profile name (THR-107 seq155). */
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
  // the freshly-registered name is registered. When `requirePresent` is true
  // (built-in + BinaryStage), a `present:true` match is required.  When
  // `requirePresent` is false (ProfileStage), name-only appearance permits
  // advancing to the next stage.  The binary registry (executors.json) is the
  // sole availability source (THR-107 seq155).
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

/** Build the adapter-backed connect prompt (THR-107 seq184). The prompt
 *  directs the candidate CLI to FETCH the canonical contract reference
 *  FIRST (a self-contained v1 daemon endpoint with the authoritative
 *  AdapterInput/AdapterOutput JSON Schemas), build a v1 wrapper, complete
 *  the conformance challenge, and submit via POST /runtime/adapters/submit.
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
    `# 0. FETCH the canonical contract reference FIRST:`,
    `#    GET ${base}/runtime/adapters/contract-reference`,
    `#    This returns the authoritative v1 AdapterInput and AdapterOutput`,
    `#    JSON Schemas (generated from the shipping Pydantic models), plus`,
    `#    version, output rules, dependency manifest schema, token-metering`,
    `#    expectations, and submission metadata. The response includes your`,
    `#    canonical_adapter_id — you MUST use that exact value for`,
    `#    adapter_metadata.adapter in every AdapterOutput (never a display`,
    `#    name or provider string). Follow these schemas — the`,
    `#    server-derived schema is canonical.`,
    ``,
    `# 1. Create a v1 adapter wrapper executable. Exact I/O contract:`,
    `#    - Read exactly one v1 AdapterInput JSON object from stdin`,
    `#    - The server prepares/creates the workspace directory — you do`,
    `#      not need to create it`,
    `#    - Invoke your CLI with truthful prompt, workspace, and timeout`,
    `#      context from the input`,
    `#    - You have one 30-second wall-clock deadline (including post-EOF`,
    `#      wait for your subprocess to exit after closing stdout)`,
    `#    - Write exactly one v1 AdapterOutput JSON object to stdout`,
    `#    - No non-JSON diagnostics on stdout`,
    `#    - Use stderr for all diagnostics, logging, and errors`,
    `#    - Exit after writing the output (single-invocation wrapper)`,
    `#    - Max output: 1 MB stdout, 1 MB stderr`,
    `#    - A syntactically valid AdapterOutput with success=false returns`,
    `#      a 4xx error with your error field and stderr_tail for debugging`,
    `#    - Declare EVERY child executable as an immutable dependency:`,
    `#      absolute path (never a bare command name), SHA-256 of the file.`,
    `#      The adapter wrapper MUST invoke child executables by their`,
    `#      exact declared absolute paths — the runtime scrubs PATH for`,
    `#      manifest-adapters so ambient PATH resolution is not available.`,
    `#    - Declare token_metering capability ONLY if your real conformance`,
    `#      probe emits valid non-null token_usage with at least one numeric`,
    `#      accounting field (zero is legitimate).`,
    `#    - Changing the wrapper, dependencies, or capabilities requires`,
    `#      re-submission and founder re-approval.`,
    `#    Follow the schemas from step 0 exactly. The self-test fixture in`,
    `#    the contract reference shows a minimal valid input/output pair.`,
    ``,
    `# 2. Complete the conformance challenge — POST each step id to`,
    `#    ${base}/executors/runtime/conformance-checkin`,
    `#    body {"step_id":"<id>"} for each of:`,
    `#      workspace_access   loopback_reachable   cli_callback`,
    `#    Then for emit_envelope, POST a sample legacy v1 result-envelope`,
    `#    (required by the registration token challenge — this is NOT an`,
    `#    AdapterOutput sample):`,
    `#      body {"step_id":"emit_envelope",`,
    `#           "envelope":{"envelope_version":1,`,
    `#                       "token_usage":{"input_tokens":1,"output_tokens":1}}}`,
    ``,
    `# 3. Submit your adapter — POST to`,
    `#    ${base}/runtime/adapters/submit`,
    `#    body {"executable":"<absolute-path-to-wrapper>","version":"1.0.0",`,
    `#         "capabilities":["token_metering"],"workspace_adapter":"pi",`,
    `#         "dependency_manifest_version":1,`,
    `#         "dependencies":[{"executable":"<absolute-path>","sha256":"<hex>"}]}`,
    ``,
    `# Submission creates ONLY the exact PENDING adapter. Founder approval`,
    `# is a separate, Settings-only step. When the founder approves, the`,
    `# server atomically approves AND connects the "${name}" profile — one`,
    `# action, no follow-up bind needed.`,
    `# No auto-approval, no token disclosure beyond this prompt.`,
    ``,
    `# This token is valid for about 30 minutes. This screen updates live.`,
  ].join('\n');
}

/** Adapter-backed connection status matching the adapter lifecycle. */
export type AdapterState =
  | { stage: 'form' }
  | { stage: 'waiting'; name: string; token: string; expired: boolean; adapterId: string }
  | { stage: 'submitted'; name: string; adapterId: string; status: string }
  | { stage: 'connected'; name: string; adapterId: string };

/** Shared hook for the adapter-backed custom-CLI connection (THR-107 seq141).
 *  Mints an adapter-purpose token → CLI creates/submits v1 adapter wrapper
 *  → UI polls adapter status → Connected when server reports already_bound.
 *  Normal intended-profile approval is atomic (seq237): the server
 *  approves and connects in one transaction — no client-side bind. */
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

  // Transition: submitted → connected when server confirms atomically bound.
  // No client-side bind — approval is an atomic server transaction (seq237).
  // The server-authoritative ``eligibility`` value is the single source of truth.
  useEffect(() => {
    if (state.stage !== 'submitted') return;
    if (adapterEntry && adapterEntry.eligibility === 'already_bound') {
      setState({ stage: 'connected', name, adapterId: adapterIdForPoll });
      onConnected({ name, path: null, via: 'custom' });
    }
  }, [adapterEntry, state.stage, adapterIdForPoll, onConnected, name]);

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

  return { state, name, token, adapterId: adapterIdForPoll, mint, start, regenerate, back };
}

/* ------------------------------------------------------------------ */
/*  Durable recovery — discover APPROVED unbound adapters from server  */
/* ------------------------------------------------------------------ */

/** An adapter that is APPROVED but not yet bound to a profile. */
export interface RecoverableAdapter {
  adapterId: string;
  profileName: string;
  executable: string;
  workspaceAdapter: string;
}

export type RecoveryState =
  | { stage: 'loading' }
  | { stage: 'empty' }
  | { stage: 'ready'; adapters: RecoverableAdapter[] }
  | { stage: 'error'; message: string };

/**
 * Discover APPROVED bindable adapters from durable server state using the
 * server-authoritative ``eligibility`` field (TASK-3784).  The browser MUST
 * NOT recompute hash/tamper eligibility — this hook uses the server's
 * ``eligibility`` value directly.
 *
 * Used by Settings → Executors to show
 * a truthful "Bind <profile>" recovery action after refresh or a
 * new session.  Only adapters with ``eligibility === 'ready_to_bind'``
 * are shown as recoverable.
 */
export function useAdapterRecovery(): {
  state: RecoveryState;
  refetch: () => void;
} {
  const [recoveryState, setRecoveryState] = useState<RecoveryState>({ stage: 'loading' });

  const adaptersQuery = useQuery({
    queryKey: ['adapters', 'list'],
    queryFn: () =>
      import('@/lib/api').then(({ adapters }) => adapters.listAdapters()),
    staleTime: 15_000,
  });

  useEffect(() => {
    if (adaptersQuery.isLoading) {
      setRecoveryState({ stage: 'loading' });
      return;
    }
    if (adaptersQuery.isError) {
      setRecoveryState({
        stage: 'error',
        message: 'Could not load adapter state from the daemon.',
      });
      return;
    }

    const adapters = adaptersQuery.data ?? [];

    // Use the server-authoritative eligibility field — never recompute.
    const recoverable: RecoverableAdapter[] = [];
    for (const a of adapters) {
      if (a.eligibility !== 'ready_to_bind') continue;
      recoverable.push({
        adapterId: a.id,
        profileName: a.intended_profile_name ?? '',
        executable: a.executable,
        workspaceAdapter: a.workspace_adapter,
      });
    }

    if (recoverable.length === 0) {
      setRecoveryState({ stage: 'empty' });
    } else {
      setRecoveryState({ stage: 'ready', adapters: recoverable });
    }
  }, [adaptersQuery.data, adaptersQuery.isLoading, adaptersQuery.isError]);

  const refetch = (): void => {
    void adaptersQuery.refetch();
  };

  return { state: recoveryState, refetch };
}
