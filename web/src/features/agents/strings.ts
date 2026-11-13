/**
 * Agents presentation helpers (THR-118 W4c).
 *
 * `classifyAgentError` is the same F1 diagnostic boundary as
 * `classifyWorkHoursError` / `classifyJobError`: every error display site in
 * the Agents route family holds a locale-neutral `AgentErrorView` and renders
 * it through `t` on every render, so mapped copy re-translates in place on a
 * locale switch while daemon diagnostics stay byte-for-byte verbatim.
 *
 * The create-agent route returns three product codes the UI owns copy for
 * (`agent_exists`, `team_exists`, `unknown_team`); their templates take the
 * submitted `name` / `newTeam` / `team` as params via `renderAgentError`.
 */
import type { MessageKey, MessageParams } from '@/lib/i18n';

export type Translate = (key: MessageKey, params?: MessageParams) => string;

const AGENT_ERROR_KEYS: Record<string, MessageKey> = {
  agent_exists: 'agents.error.agentExists',
  team_exists: 'agents.error.teamExists',
  unknown_team: 'agents.error.unknownTeam',
  invalid_reasoning_effort: 'agents.error.invalidEffort',
  reasoning_executor_changed: 'agents.error.effortExecutorChanged',
};

export type AgentErrorView = { kind: 'message'; key: MessageKey } | { kind: 'raw'; text: string };

/**
 *  - a recognized product code -> its catalog key;
 *  - any other non-empty code -> the raw code, verbatim;
 *  - no/empty code but a non-empty string diagnostic -> that text, verbatim
 *    (an ApiError-shaped value's string `detail`, or a plain Error's message /
 *    a thrown string; ApiError's synthetic 'API <status>' message is never one);
 *  - otherwise -> the localized `fallback`, so an error is never blank.
 */
export function classifyAgentError(err: unknown, fallback: MessageKey): AgentErrorView {
  const code = (err as { code?: string } | null | undefined)?.code;
  if (code) {
    if (Object.prototype.hasOwnProperty.call(AGENT_ERROR_KEYS, code)) {
      return { kind: 'message', key: AGENT_ERROR_KEYS[code] };
    }
    return { kind: 'raw', text: code };
  }
  const diagnostic = rawDiagnostic(err);
  return diagnostic === null ? { kind: 'message', key: fallback } : { kind: 'raw', text: diagnostic };
}

function rawDiagnostic(err: unknown): string | null {
  let text: unknown = null;
  if (typeof err === 'string') text = err;
  else if (err !== null && typeof err === 'object' && 'detail' in err) text = err.detail;
  else if (err instanceof Error) text = err.message;
  return typeof text === 'string' && text.trim() !== '' ? text : null;
}

export function renderAgentError(view: AgentErrorView, t: Translate, params?: MessageParams): string {
  return view.kind === 'raw' ? view.text : t(view.key, params);
}

/** Prompt failures keep neutral state and require a fresh inspection before another PUT. */
export function classifySystemPromptError(err: unknown): MessageKey {
  const code = (err as { code?: string } | null)?.code;
  if (code === 'stale_agent_revision') return 'agents.prompt.conflict';
  if (code === 'agent_not_found') return 'agents.prompt.disappeared';
  if (code === 'expected_revision_required') return 'agents.prompt.unavailable';
  if (code === 'system_prompt_reconciliation_failed') return 'agents.prompt.reconciliationFailed';
  if (code === 'system_prompt_audit_failed') return 'agents.prompt.auditFailed';
  return 'agents.prompt.verifyFailed';
}

/** Server refusals the retire dialog owns a headline for; every other code stays verbatim. */
const RETIRE_REFUSAL_KEYS: Record<string, MessageKey> = {
  agent_not_quiescent: 'agents.retire.refusal.notQuiescent',
  team_not_empty: 'agents.retire.refusal.teamNotEmpty',
};

/**
 * Locale-neutral view of a retire failure. The daemon is the only eligibility
 * authority: its code, message/reason, blocking work and team members are
 * carried verbatim; only the two headline codes above get catalog copy.
 */
export interface RetireErrorView {
  headline: AgentErrorView;
  /** Server `message`/`reason`, shown under a catalog headline. */
  serverMessage: string | null;
  /** `<kind> <id>` per blocking item, in server order. */
  conflicts: string[];
  members: string[];
  /** Server code, when the headline does not already show it. */
  code: string | null;
  /**
   * `refused`: the server answered and declined, so the agent is unchanged.
   * `unknown`: no answer, a server fault, or 404 (the agent may already be
   * retired) — only a fresh roster read says whether the agent is still active.
   */
  outcome: 'refused' | 'unknown';
}

const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

export function classifyRetireError(err: unknown): RetireErrorView {
  const source = (err ?? {}) as { code?: unknown; detail?: unknown; status?: unknown };
  const status = typeof source.status === 'number' ? source.status : null;
  const detail = (source.detail !== null && typeof source.detail === 'object' ? source.detail : {}) as {
    message?: unknown; reason?: unknown; conflicts?: unknown; members?: unknown;
  };
  const code = text(source.code);
  const message = text(detail.message) ?? text(detail.reason);
  const known = code !== null && Object.prototype.hasOwnProperty.call(RETIRE_REFUSAL_KEYS, code);
  const conflicts = Array.isArray(detail.conflicts)
    ? detail.conflicts.flatMap((item: unknown) => {
      const entry = (item ?? {}) as { kind?: unknown; id?: unknown };
      const id = typeof entry.id === 'number' ? String(entry.id) : text(entry.id);
      return id === null ? [] : [[text(entry.kind), id].filter(Boolean).join(' ')];
    })
    : [];
  const members = Array.isArray(detail.members)
    ? detail.members.flatMap((item: unknown) => (text(item) === null ? [] : [item as string]))
    : [];
  return {
    headline: known ? { kind: 'message', key: RETIRE_REFUSAL_KEYS[code] }
      : message !== null ? { kind: 'raw', text: message }
      : classifyAgentError(err, 'agents.retire.failed'),
    serverMessage: known ? message : null,
    conflicts,
    members,
    code: known || message !== null ? code : null,
    outcome: status !== null && status >= 400 && status < 500 && status !== 404 ? 'refused' : 'unknown',
  };
}
