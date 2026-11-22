import type { RemoteConfigOperation } from './policy';

export {
  REMOTE_CONFIG_ENTRIES,
  REMOTE_READ_ONLY_CODE,
  isRemoteConfigOperation,
  isRemoteReadOnlyError,
  matchesRemoteConfigRequest,
  type PolicyMethod,
  type RemoteConfigEntry,
  type RemoteConfigOperation,
} from './policy';
export { useRemoteHost, useRemoteReadOnly } from './hooks';

/** Policy operations named by the UI (exact v1 templates). */
export const REMOTE_OPS = {
  orgSettings: 'PUT /api/v1/orgs/{slug}/settings/org',
  teamsSettings: 'PUT /api/v1/orgs/{slug}/settings/teams',
  removeBinary: 'DELETE /api/v1/executor-binaries/{kind}',
  removeProfile: 'DELETE /api/v1/executors/runtime/profiles/{name}',
  customCliRetry: 'POST /api/v1/runtime/custom-cli/{operation_id}/retry',
  customCliForget: 'POST /api/v1/runtime/custom-cli/{operation_id}/forget',
  createOrg: 'POST /api/v1/orgs',
  runtime: 'POST /api/v1/runtime',
  createAgent: 'POST /api/v1/orgs/{slug}/agents',
  initAgents: 'POST /api/v1/orgs/{slug}/agents/init',
  agentModel: 'PUT /api/v1/orgs/{slug}/agents/{agent_name}/model',
  assignSkill: 'POST /api/v1/orgs/{slug}/agents/{agent_id}/skills/{skill_id}/assign',
  createSkill: 'POST /api/v1/orgs/{slug}/skills',
  editSkill: 'PATCH /api/v1/orgs/{slug}/skills/{skill_id}',
  createCustomSkill: 'POST /api/v1/orgs/{slug}/custom-skills',
  patchCustomSkill: 'PATCH /api/v1/orgs/{slug}/custom-skills/{skill_id}',
  customSkillVersion: 'POST /api/v1/orgs/{slug}/custom-skills/{skill_id}/versions',
  retireCustomSkill: 'POST /api/v1/orgs/{slug}/custom-skills/{skill_id}/retire',
  restoreCustomSkill: 'POST /api/v1/orgs/{slug}/custom-skills/{skill_id}/restore',
  customSkillEligibility: 'PUT /api/v1/orgs/{slug}/custom-skills/{skill_id}/eligibility',
  editSchedule: 'PATCH /api/v1/orgs/{slug}/schedules/{schedule_id}',
} as const satisfies Record<string, RemoteConfigOperation>;
