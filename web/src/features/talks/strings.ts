export const TALKS_ERROR_STRINGS: Record<string, string> = {
  talk_already_open: 'An open talk with this agent already exists.',
  talk_not_open: 'This talk is no longer open.',
  unknown_kb_slug: "That KB slug doesn't exist. Add it with `grassland kb add` first.",
  empty_brief: 'Brief is required.',
  empty_team: 'Team cannot be blank.',
  empty_target_agent: 'Target agent cannot be blank.',
  dispatcher_team_unknown: "The dispatching agent isn't on a team.",
  talk_dispatch_must_be_self:
    "Dispatch from a talk only spawns a task for the talk's own agent. " +
    'For cross-agent work, end the talk and open a thread.',
  talk_dispatch_team_override_forbidden:
    "The talk's agent can only dispatch within their own team.",
  teams_registry_unavailable: 'Teams registry is unavailable for this org.',
  unknown_agent: "That agent doesn't exist in this org.",
  not_found: 'Talk not found.',
};

export function describeTalksError(
  code: string | null | undefined,
  fallback?: string,
): string {
  if (code && TALKS_ERROR_STRINGS[code]) return TALKS_ERROR_STRINGS[code];
  return fallback ?? code ?? 'Something went wrong.';
}
