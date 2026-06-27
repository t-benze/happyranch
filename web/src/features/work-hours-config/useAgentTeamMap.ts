/**
 * Build an agent-name → team-name map from the live teams roster. Agents not on
 * any team map to `null`. Used to scope the reconciliation (team tier lookup)
 * and the org/team impact preview.
 */
import { useMemo } from 'react';
import { useTeamsList } from '@/hooks/teams';

export function useAgentTeamMap(): Record<string, string | null> {
  const teamsQuery = useTeamsList();
  return useMemo(() => {
    const map: Record<string, string | null> = {};
    for (const team of teamsQuery.data?.teams ?? []) {
      if (team.manager) map[team.manager] = team.name;
      for (const worker of team.workers) map[worker] = team.name;
    }
    return map;
  }, [teamsQuery.data?.teams]);
}
