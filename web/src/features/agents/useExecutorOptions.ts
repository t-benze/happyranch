/**
 * Shared executor-option derivation for create and edit.
 *
 * Both AddAgentDialog (create) and AgentDetailPane (edit) consume the
 * same live data sources — `/health/prereqs` and `/executors/runtime/profiles`
 * — to produce a unified, non-hard-coded option list. This hook is the single
 * source; create and edit cannot drift.
 */
import { useMemo } from 'react';
import { usePrereqs } from '@/hooks/health';
import { useRuntimeProfiles } from '@/hooks/runtime-executors';

/** One selectable executor option rendered in the UI. */
export interface ExecutorOption {
  name: string;
  present: boolean;
  /** The kind — builtin, custom, or unregistered_builtin (unavailable). */
  kind: 'builtin' | 'custom' | 'unregistered_builtin';
  hint: string | null;
}

export interface ExecutorOptionsResult {
  selectable: ExecutorOption[];
  unavailable: ExecutorOption[];
  state: 'loading' | 'error' | 'empty' | 'ready';
}

/**
 * Derive the executor option list from the live daemon — never hard-coded.
 *
 * - Built-ins from `/health/prereqs` that are launchable (present=true).
 * - Built-ins that are NOT launchable → unavailable, shown disabled.
 * - Custom profiles from `/executors/runtime/profiles` — all are SELECTABLE
 *   when present=true, and disabled-but-visible when present=false.
 * - On API error, emits `state='error'`; caller must not invent fallbacks.
 *
 * The hook is pure derivation; the caller decides whether to retain an
 * agent's historical executor that is absent from the live lists, or to
 * auto-select a default when the user's pick disappears on refetch.
 */
export function useExecutorOptions(): ExecutorOptionsResult {
  const prereqsQuery = usePrereqs();
  const profilesQuery = useRuntimeProfiles();

  return useMemo<ExecutorOptionsResult>(() => {
    if (prereqsQuery.isLoading || profilesQuery.isLoading) {
      return { selectable: [], unavailable: [], state: 'loading' };
    }
    if (prereqsQuery.isError || profilesQuery.isError) {
      return { selectable: [], unavailable: [], state: 'error' };
    }

    const prereqs = prereqsQuery.data?.prereqs ?? [];
    const customProfiles = profilesQuery.data?.profiles ?? [];

    // Names of all registered custom profiles — used to distinguish
    // built-in-or-future-registry executors from known custom ones.
    const customNameSet = new Set(customProfiles.map((p) => p.name));

    // Custom profiles from runtime/profiles: registered by definition.
    // present=true → selectable; present=false → visible but disabled.
    const selectableCustoms: ExecutorOption[] = [];
    const unavailableCustoms: ExecutorOption[] = [];
    for (const p of customProfiles) {
      const opt: ExecutorOption = {
        name: p.name,
        present: p.present,
        kind: 'custom' as const,
        hint: null,
      };
      if (p.present) {
        selectableCustoms.push(opt);
      } else {
        unavailableCustoms.push(opt);
      }
    }

    // Built-ins: every prereq whose name is NOT a known custom profile.
    // Deduplicate against the custom set so a prereq that also appears as
    // a custom profile is represented only once (as custom).
    const selectableBuiltins: ExecutorOption[] = [];
    const unavailableBuiltins: ExecutorOption[] = [];
    for (const p of prereqs) {
      if (customNameSet.has(p.tool)) continue; // covered by the customs list
      const opt: ExecutorOption = {
        name: p.tool,
        present: p.present,
        kind: p.present ? 'builtin' : 'unregistered_builtin',
        hint: p.hint,
      };
      if (p.present) {
        selectableBuiltins.push(opt);
      } else {
        unavailableBuiltins.push(opt);
      }
    }

    const selectable = [...selectableBuiltins, ...selectableCustoms];
    const unavailable = [...unavailableBuiltins, ...unavailableCustoms];

    if (selectable.length === 0) {
      return { selectable: [], unavailable, state: 'empty' };
    }
    return { selectable, unavailable, state: 'ready' };
  }, [prereqsQuery, profilesQuery]);
}
