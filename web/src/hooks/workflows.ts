import { useData } from '@/design-system/providers/DataContext';
export const useWorkflowActivations: ReturnType<typeof useData>['workflows']['useActivations'] = () => useData().workflows.useActivations();
export const useWorkflowReviewDetail: ReturnType<typeof useData>['workflows']['useReviewDetail'] = (id, history) => useData().workflows.useReviewDetail(id, history);
export const useHumanReviewResponse: ReturnType<typeof useData>['workflows']['useHumanResponse'] = () => useData().workflows.useHumanResponse();
export const useOwnerReviewReturn: ReturnType<typeof useData>['workflows']['useOwnerReturn'] = () => useData().workflows.useOwnerReturn();
