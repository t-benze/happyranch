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
