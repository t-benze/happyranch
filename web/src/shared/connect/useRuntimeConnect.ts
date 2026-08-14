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
import {
  directConnect,
  executorBinaries,
  health as healthApi,
  settings as settingsApi,
} from '@/lib/api';

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
      `set -e`,
      ``,
      `TOKEN=${TOKEN}`,
      `BASE=${BASE}`,
      ``,
      `# 1. Discover your own absolute binary path`,
      `BIN=$(command -v ${BIN_NAME} 2>/dev/null || which ${BIN_NAME} 2>/dev/null || true)`,
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
      `if ! RESP=$(curl --fail-with-body -sS -X POST "$BASE/executors/runtime/conformance-checkin" \\`,
      `  -H "Authorization: Bearer $TOKEN" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '{"step_id":"emit_envelope","envelope":{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1,"model":"custom-cli"}}}'); then`,
      `  echo "$RESP"`,
      `  exit 1`,
      `fi`,
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
    `#    body {"step_id":"emit_envelope","envelope":{"envelope_version":1,"token_usage":{"input_tokens":1,"output_tokens":1,"model":"custom-cli"}}}`,
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


/* ------------------------------------------------------------------ */
/*  DIRECT CONNECT — instant, no founder-approval step (THR-107 slice 3) */
/*  mint (name + workspace_adapter_id) -> wrapper path from status ->   */
/*  candidate CLI POSTs /connect itself -> poll status -> commit ->     */
/*  Connected. Replaces the deleted adapter-submit/PENDING/approve flow */
/*  and the deleted durable-recovery hook — this is the only custom-    */
/*  adapter connect path in the normal UI now.                          */
/* ------------------------------------------------------------------ */

/** The four first-party workspace-bootstrap conventions a direct-connect
 *  wrapper declares one of at /connect time (never chosen by the founder) —
 *  mirrors the daemon's FIRST_PARTY_WORKSPACE_ADAPTER_IDS. */
export const WORKSPACE_ADAPTER_IDS = ['claude', 'codex', 'opencode', 'pi'] as const;
export type WorkspaceAdapterId = (typeof WORKSPACE_ADAPTER_IDS)[number];

/** Build the direct-connect prompt (THR-107 slice 3). Unlike the legacy
 *  adapter-submit prompt, there is no separate conformance-checkin dance
 *  and no PENDING/approval wait. `/connect` is receipt-only; trusted daemon
 *  projection later performs the bounded behavioral probe before connecting.
 *  `wrapperDestination` is the
 *  LITERAL server-returned path from GET /runtime/custom-cli/status —
 *  do NOT derive, fallback, or guess this path client-side.
 *
 *  The wrapper itself declares `workspace_adapter_id` in the manifest body
 *  — this selects which convention (Claude-style `.claude/settings.json` +
 *  `CLAUDE.md`, or AGENTS.md-style for codex/opencode/pi) HappyRanch uses
 *  to bootstrap agent workspaces assigned to this CLI later. The founder
 *  never picks this; only the wrapper author knows which convention their
 *  CLI expects. */
export function buildDirectConnectPrompt(
  name: string,
  token: string,
  origin: string,
  wrapperDestination: string,
): string {
  const base = `${origin}/api/v1`;
  const TOKEN = shQuote(token);
  const BASE = shQuote(base);
  const WRAPPER = shQuote(wrapperDestination);
  return [
    `# Connect "${name}" to HappyRanch directly — no approval step.`,
    `# Do all of this in one run, then stop.`,
    ``,
    `set -e`,
    `TOKEN=${TOKEN}`,
    `BASE=${BASE}`,
    `WRAPPER=${WRAPPER}`,
    ``,
    `# 1. Create a v1 adapter wrapper executable at exactly:`,
    `#      ${wrapperDestination}`,
    `#    This is the LITERAL daemon-issued path — no other location,`,
    `#    symlink, or alternate filename is accepted. Exact I/O contract:`,
    `#    - Read exactly one v1 AdapterInput JSON object from stdin`,
    `#    - Forward the ENTIRE AdapterInput.prompt through your provider's`,
    `#      ordinary one-shot launch path, with truthful workspace and timeout`,
    `#      context from the input (the server prepares the workspace dir)`,
    `#    - Write exactly one v1 AdapterOutput JSON object to stdout,`,
    `#      nothing else on stdout — diagnostics go to stderr`,
    `#    - Propagate the provider's terminal error, return code, and agent`,
    `#      session id faithfully; never emit success without a real provider`,
    `#      terminal response`,
    `#    - Exit after writing the output (single-invocation wrapper)`,
    `#    - Before /connect, locally send your wrapper a fresh opaque canary in`,
    `#      AdapterInput.prompt and prove the real terminal provider response`,
    `#      returns that exact canary in AdapterOutput.result.text`,
    `chmod +x "$WRAPPER"`,
    ``,
    `# 2. Declare EVERY child executable your wrapper invokes as an`,
    `#    immutable dependency: absolute path (never a bare command name)`,
    `#    plus its SHA-256. HappyRanch never selects an agentic CLI via`,
    `#    ambient PATH — replace CHILD below with your CLI's absolute path.`,
    `CHILD=/absolute/path/to/your-cli`,
    `WRAPPER_SHA=$(shasum -a 256 "$WRAPPER" | cut -d' ' -f1)`,
    `CHILD_SHA=$(shasum -a 256 "$CHILD" | cut -d' ' -f1)`,
    ``,
    `# 3. Pick the workspace-bootstrap convention agents on this CLI should`,
    `#    use. This is about YOUR CLI's expectations, not HappyRanch's —`,
    `#    pick whichever bootstrap file format your CLI actually reads:`,
    `#      claude    -> .claude/settings.json + CLAUDE.md`,
    `#      codex     -> AGENTS.md`,
    `#      opencode  -> AGENTS.md + opencode.json`,
    `#      pi        -> AGENTS.md`,
    `#    If your CLI has no opinion, "pi" is a safe default (AGENTS.md).`,
    `WORKSPACE_ADAPTER_ID=pi`,
    ``,
    `# 4. POST the manifest — this receipt-only call validates wrapper`,
    `#    integrity and creates the connection record; it starts no subprocess.`,
    `curl --fail-with-body -sS -X POST "$BASE/runtime/custom-cli/connect" \\`,
    `  -H "Authorization: Bearer $TOKEN" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d "{\\"metadata\\":{},\\"manifest\\":{\\"manifest_version\\":2,\\"wrapper_sha256\\":\\"$WRAPPER_SHA\\",\\"workspace_adapter_id\\":\\"$WORKSPACE_ADAPTER_ID\\",\\"upgradeable_children\\":[{\\"slot\\":\\"cli\\",\\"executable\\":\\"$CHILD\\",\\"version_probe_argv\\":[\\"$CHILD\\",\\"--version\\"]}]}}"`,
    `echo ""`,
    ``,
    `# This token is valid for about 30 minutes. This screen updates live —`,
    `# trusted daemon commit/projection then runs one bounded behavioral probe`,
    `# and finishes connecting automatically. No founder-approval step.`,
  ].join('\n');
}

/** Direct-connect state machine. */
export type DirectConnectState =
  | { stage: 'form' }
  | { stage: 'waiting'; name: string; token: string; expired: boolean; wrapperDestination: string }
  | { stage: 'committing'; name: string; wrapperDestination: string }
  | { stage: 'connected'; name: string; wrapperDestination: string }
  | { stage: 'failed'; name: string; wrapperDestination: string; operationId: string; reason: string };

/** Mint-time value for the RuntimeRegistrationTokenMintRequest's
 *  workspace_adapter_id field — this ONLY activates the daemon's Slice-1A
 *  direct-authority mint path; it is never read for real bootstrap
 *  behavior. The wrapper's own /connect manifest carries the value that
 *  actually matters (see buildDirectConnectPrompt). Any of the four IDs
 *  works here since it's discarded downstream — kept as a named constant
 *  so the "this is a trigger, not a real choice" intent is explicit at
 *  the call site rather than a bare magic string. */
const MINT_ACTIVATION_TRIGGER: WorkspaceAdapterId = 'pi';

/** Shared hook for the direct-connect custom-CLI flow (THR-107 slice 3).
 *  Mints an adapter-purpose token (workspace_adapter_id is a fixed
 *  internal trigger here, not founder-chosen — see
 *  MINT_ACTIVATION_TRIGGER) → immediately reads GET .../status for the
 *  deterministic wrapper destination → the candidate CLI POSTs /connect
 *  itself, declaring its OWN workspace_adapter_id in the manifest
 *  (server-side, invisible to this hook — it only polls) → the daemon
 *  projects the received operation independently → this hook observes the
 *  terminal committed or failed status and updates the UI. */
export function useDirectConnect({
  onConnected,
}: {
  onConnected: (c: Connected) => void;
}) {
  const [state, setState] = useState<DirectConnectState>({ stage: 'form' });
  const [expiresAt, setExpiresAt] = useState(0);

  const mint = useMutation({
    mutationFn: async (name: string) => {
      const resp = await settingsApi.mintRuntimeRegistrationToken({
        name,
        purpose: 'adapter',
        intended_profile_name: name,
        workspace_adapter_id: MINT_ACTIVATION_TRIGGER,
      });
      const status = await directConnect.getStatus(name);
      return { ...resp, wrapperDestination: status.wrapper_destination };
    },
    onSuccess: (resp, name) => {
      setExpiresAt(resp.expires_at);
      setState({
        stage: 'waiting',
        name,
        token: resp.token,
        expired: false,
        wrapperDestination: resp.wrapperDestination,
      });
    },
  });

  // Time-based expiry
  useEffect(() => {
    if (state.stage !== 'waiting' || state.expired || !expiresAt) return;
    const ms = expiresAt * 1000 - Date.now();
    if (ms <= 0) {
      setState((s) => (s.stage === 'waiting' ? { ...s, expired: true } : s));
      return;
    }
    const t = window.setTimeout(() => {
      setState((s) => (s.stage === 'waiting' ? { ...s, expired: true } : s));
    }, ms);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.stage, expiresAt]);

  const pollName = (state.stage === 'waiting' && !state.expired) || state.stage === 'committing'
    ? state.name
    : '';
  const { data: statusData } = useQuery({
    queryKey: ['direct-connect', 'status', pollName],
    queryFn: () => directConnect.getStatus(pollName),
    enabled: pollName !== '',
    refetchInterval: pollName !== '' ? 2500 : false,
  });

  const commitMutation = useMutation({
    mutationFn: (operationId: string) => directConnect.commit(operationId),
  });

  // A daemon-projected candidate CLI receipt moves waiting -> committing.
  // The daemon owns projection; this hook only observes its terminal status.
  useEffect(() => {
    if (state.stage === 'waiting') {
      if (state.expired || !statusData?.operation_id || statusData.profile_state == null) return;
      setState({
        stage: 'committing',
        name: state.name,
        wrapperDestination: state.wrapperDestination,
      });
      return;
    }
    if (state.stage !== 'committing' || !statusData?.operation_id) return;

    if (statusData.profile_state === 'committed') {
      setState({
        stage: 'connected',
        name: state.name,
        wrapperDestination: state.wrapperDestination,
      });
      onConnected({ name: state.name, path: state.wrapperDestination, via: 'custom' });
    } else if (statusData.profile_state === 'failed') {
      setState({
        stage: 'failed',
        name: state.name,
        wrapperDestination: state.wrapperDestination,
        operationId: statusData.operation_id,
        reason: statusData.reason ?? 'The connection could not be completed.',
      });
    } else if (statusData.profile_state === 'planned' || statusData.profile_state == null) {
      // Daemon projection remains non-terminal; keep polling while committing.
    }
  }, [onConnected, state, statusData]);

  const start = (name: string): void => {
    if (name && !mint.isPending) mint.mutate(name);
  };
  const regenerate = (): void => {
    if (state.stage === 'waiting' && !mint.isPending) {
      mint.mutate(state.name);
    }
  };
  const retryCommit = (): void => {
    if (state.stage !== 'failed') return;
    const { name, wrapperDestination, operationId } = state;
    setState({ stage: 'committing', name, wrapperDestination });
    commitMutation.mutate(operationId, {
      onSuccess: (resp) => {
        if (resp.profile_state === 'committed') {
          setState({ stage: 'connected', name, wrapperDestination });
          onConnected({ name, path: wrapperDestination, via: 'custom' });
        } else {
          setState({
            stage: 'failed', name, wrapperDestination, operationId,
            reason: resp.reason ?? 'The connection could not be completed.',
          });
        }
      },
      onError: () => {
        setState({
          stage: 'failed', name, wrapperDestination, operationId,
          reason: 'Could not reach the daemon to finish connecting.',
        });
      },
    });
  };
  const back = (): void => {
    setState({ stage: 'form' });
    setExpiresAt(0);
    mint.reset();
  };

  return { state, mint, start, regenerate, retryCommit, back };
}
