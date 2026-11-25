/** Provider-aware task-attribute VALUE hooks (THR-390 PR7); network work belongs to the provider. */
import { useData } from '@/design-system/providers/DataContext';

export const useTaskAttributes = (taskId: string, fresh = false) =>
  useData().taskAttributeValues.useTaskAttributes(taskId, fresh);
export const useTaskAttributeValueActions = (taskId: string) =>
  useData().taskAttributeValues.useTaskAttributeValueActions(taskId);
