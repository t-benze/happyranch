/** Provider-aware task-attribute definition hooks (THR-390 PR6); network work belongs to the provider. */
import { useData } from '@/design-system/providers/DataContext';

export const useDefinitionList = () => useData().taskAttributes.useDefinitionList();
export const useDefinitionReads = (ids: readonly string[], enabled: boolean) =>
  useData().taskAttributes.useDefinitionReads(ids, enabled);
export const useDefinition = (definitionId: string | null) => useData().taskAttributes.useDefinition(definitionId);
export const useDefinitionVersions = (definitionId: string, versions: readonly number[], enabled: boolean) =>
  useData().taskAttributes.useDefinitionVersions(definitionId, versions, enabled);
export const useTaskAttributeActions = () => useData().taskAttributes.useTaskAttributeActions();
