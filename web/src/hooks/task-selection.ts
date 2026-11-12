/**
 * Per-task model / reasoning-effort options for one TARGET agent (THR-356).
 *
 * Nothing here is a catalog. Everything is derived from live daemon reads the
 * agent settings page already uses:
 *  - the agent's executor and defaults from the agents list;
 *  - launchability and the executor's own effort values from
 *    `useExecutorOptions` (`/health/prereqs` + `/executors/runtime/profiles`);
 *  - a custom executor's declared model/effort templates from the existing
 *    adapter list, joined through the profile's `command_adapter_id`.
 *
 * The daemon stays authoritative: these options only decide what the form
 * offers and why a field is disabled. A refused choice is shown, never hidden.
 */
import { useMemo } from 'react';
import { useAgentsList } from '@/hooks/agents';
import { useAdapters } from '@/hooks/adapters';
import { useExecutorOptions } from '@/hooks/executor-options';
import { useRuntimeProfiles } from '@/hooks/runtime-executors';

export type TaskSelectionState =
  /** No target agent is chosen yet. */
  | 'no-agent'
  | 'loading'
  | 'error'
  /** The agent is not in the current roster. */
  | 'unknown-agent'
  /** The agent's executor is not registered/launchable on this machine. */
  | 'executor-unavailable'
  | 'ready';

/** Why a field cannot be edited; `null` means it can. */
export type TaskSelectionFieldBlock =
  | 'no-agent'
  | 'loading'
  | 'error'
  | 'unknown-agent'
  | 'executor-unavailable'
  /** A custom executor that does not declare this field. */
  | 'undeclared'
  /** OpenCode: effort needs a pinned provider/model. */
  | 'needs-pinned-model'
  | null;

export interface TaskSelectionOptions {
  state: TaskSelectionState;
  agentName: string | null;
  executor: string | null;
  executorKind: 'builtin' | 'custom' | null;
  /** The agent page's current defaults (null = no agent-level setting). */
  agentModel: string | null;
  agentEffort: string | null;
  modelBlock: TaskSelectionFieldBlock;
  /** Effort block that does not depend on the model typed in the form. */
  effortBlock: TaskSelectionFieldBlock;
  /** The executor's own effort values, as the daemon reports them. */
  effortValues: string[];
  /** True when effort additionally needs a pinned provider/model (OpenCode). */
  effortNeedsPinnedModel: boolean;
}

const CUSTOM_ADAPTER_PREFIX = 'custom-adapter:';

function blocked(
  state: Exclude<TaskSelectionState, 'ready'>,
  agentName: string | null,
): TaskSelectionOptions {
  return {
    state, agentName, executor: null, executorKind: null, agentModel: null, agentEffort: null,
    modelBlock: state, effortBlock: state, effortValues: [], effortNeedsPinnedModel: false,
  };
}

/** OpenCode's pinned form is `provider/model` with non-empty components. */
export function isPinnedProviderModel(model: string | null | undefined): boolean {
  if (!model) return false;
  const base = model.split('#')[0];
  const slash = base.indexOf('/');
  return slash > 0 && slash < base.length - 1;
}

/**
 * Effort block for the model that would actually apply (`effectiveModel` is
 * the task override when set, else the agent default).
 */
export function effortBlockFor(
  options: TaskSelectionOptions,
  effectiveModel: string | null,
): TaskSelectionFieldBlock {
  if (options.effortBlock !== null) return options.effortBlock;
  if (options.effortNeedsPinnedModel && !isPinnedProviderModel(effectiveModel)) return 'needs-pinned-model';
  return null;
}

export function useTaskSelectionOptions(agentName: string | null | undefined): TaskSelectionOptions {
  const agentsQuery = useAgentsList();
  const executorOptions = useExecutorOptions();
  const profilesQuery = useRuntimeProfiles();
  const adaptersQuery = useAdapters();

  return useMemo<TaskSelectionOptions>(() => {
    const name = agentName || null;
    if (name === null) return blocked('no-agent', null);
    if (agentsQuery.isLoading || executorOptions.state === 'loading') return blocked('loading', name);
    if (agentsQuery.isError || executorOptions.state === 'error') return blocked('error', name);
    const agent = agentsQuery.data?.agents.find((item) => item.name === name);
    if (!agent || !agent.executor) return blocked('unknown-agent', name);
    const option = [...executorOptions.selectable, ...executorOptions.unavailable]
      .find((item) => item.name === agent.executor);
    const base = {
      agentName: name, executor: agent.executor,
      agentModel: agent.model ?? null, agentEffort: agent.reasoning_effort ?? null,
    };
    if (!option || !option.present || option.kind === 'unregistered_builtin') {
      return {
        ...base, state: 'executor-unavailable', executorKind: null,
        modelBlock: 'executor-unavailable', effortBlock: 'executor-unavailable',
        effortValues: [], effortNeedsPinnedModel: false,
      };
    }
    const effortValues = option.reasoningEffortValues ?? [];
    if (option.kind === 'builtin') {
      return {
        ...base, state: 'ready', executorKind: 'builtin', modelBlock: null,
        effortBlock: effortValues.length > 0 ? null : 'undeclared',
        effortValues, effortNeedsPinnedModel: agent.executor === 'opencode',
      };
    }
    // Custom: the approved adapter's input declaration decides each field.
    if (adaptersQuery.isLoading) return { ...blocked('loading', name), ...base };
    if (adaptersQuery.isError) return { ...blocked('error', name), ...base };
    const profile = profilesQuery.data?.profiles.find((item) => item.name === agent.executor);
    const adapterId = profile?.command_adapter_id?.startsWith(CUSTOM_ADAPTER_PREFIX)
      ? profile.command_adapter_id.slice(CUSTOM_ADAPTER_PREFIX.length) : null;
    const declared = adaptersQuery.data?.find((item) => item.id === adapterId)?.input_options ?? null;
    return {
      ...base, state: 'ready', executorKind: 'custom',
      modelBlock: declared?.model_arg_template ? null : 'undeclared',
      effortBlock: declared?.effort_arg_template && effortValues.length > 0 ? null : 'undeclared',
      effortValues, effortNeedsPinnedModel: false,
    };
  }, [agentName, agentsQuery, executorOptions, profilesQuery, adaptersQuery]);
}
