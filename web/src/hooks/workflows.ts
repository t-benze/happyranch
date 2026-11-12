import { useData } from '@/design-system/providers/DataContext';
export const useSequentialDefinitions: ReturnType<typeof useData>['workflows']['useSequentialDefinitions'] = () => useData().workflows.useSequentialDefinitions();
export const useSequentialDefinition: ReturnType<typeof useData>['workflows']['useSequentialDefinition'] = (id) => useData().workflows.useSequentialDefinition(id);
export const useSequentialSettings: ReturnType<typeof useData>['workflows']['useSequentialSettings'] = (id) => useData().workflows.useSequentialSettings(id);
export const useSequentialRuns: ReturnType<typeof useData>['workflows']['useSequentialRuns'] = () => useData().workflows.useSequentialRuns();
export const useSequentialRun: ReturnType<typeof useData>['workflows']['useSequentialRun'] = (id) => useData().workflows.useSequentialRun(id);
export const useTaskSequentialWorkflow: ReturnType<typeof useData>['workflows']['useTaskSequentialWorkflow'] = (id) => useData().workflows.useTaskSequentialWorkflow(id);
export const useSequentialSettingsActions: ReturnType<typeof useData>['workflows']['useSequentialSettingsActions'] = () => useData().workflows.useSequentialSettingsActions();
export const useSequentialSettingsFence: ReturnType<typeof useData>['workflows']['useSequentialSettingsFence'] = () => useData().workflows.useSequentialSettingsFence();
