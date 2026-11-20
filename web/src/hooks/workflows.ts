import { useEffect, useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getSequentialRunV2, getTaskSequentialWorkflows, listSequentialRunsV2 } from '@/lib/api/workflowSequential';
import { useData } from '@/design-system/providers/DataContext';
export const useWorkflowSetupStatus: ReturnType<typeof useData>['workflows']['useWorkflowSetupStatus'] = () => useData().workflows.useWorkflowSetupStatus();
export const useSequentialDefinitions: ReturnType<typeof useData>['workflows']['useSequentialDefinitions'] = () => useData().workflows.useSequentialDefinitions();
export const useSequentialDefinition: ReturnType<typeof useData>['workflows']['useSequentialDefinition'] = (id) => useData().workflows.useSequentialDefinition(id);
export const useSequentialSettings: ReturnType<typeof useData>['workflows']['useSequentialSettings'] = (id) => useData().workflows.useSequentialSettings(id);
export const useSequentialRuns: ReturnType<typeof useData>['workflows']['useSequentialRuns'] = () => useData().workflows.useSequentialRuns();
export const useSequentialRun: ReturnType<typeof useData>['workflows']['useSequentialRun'] = (id) => useData().workflows.useSequentialRun(id);
export const useTaskSequentialWorkflow: ReturnType<typeof useData>['workflows']['useTaskSequentialWorkflow'] = (id) => useData().workflows.useTaskSequentialWorkflow(id);
export const useSequentialSettingsActions: ReturnType<typeof useData>['workflows']['useSequentialSettingsActions'] = () => useData().workflows.useSequentialSettingsActions();
export const useSequentialSettingsFence: ReturnType<typeof useData>['workflows']['useSequentialSettingsFence'] = () => useData().workflows.useSequentialSettingsFence();


/** Additive @2 read hooks for the S7b renderer. No settings/send custody changes. */
function useSequentialProjection<T extends { org_slug: string }>(kind: string, id: string,
  load: (slug: string, signal: AbortSignal) => Promise<T>) {
  const { slug = '' } = useParams();
  const qc = useQueryClient();
  const owner = useMemo(() => ({ slug, kind, id, active: true, sequence: 0 }), [slug, kind, id]);
  const query = useQuery({ queryKey: ['workflow-sequential', slug, 'v2', kind, id],
    queryFn: async ({ signal }) => {
      const sequence = ++owner.sequence;
      const current = () => owner.active && owner.sequence === sequence && !signal.aborted;
      try {
        const value = await load(slug, signal);
        if (!current() || value.org_slug !== slug) throw new Error('late_org_response');
        return value;
      } catch (error) {
        if (!current()) throw new Error('late_org_response');
        throw error;
      }
    }, enabled: !!slug && !!id, retry: false, staleTime: 0, gcTime: 0,
    placeholderData: undefined, refetchOnMount: 'always', refetchOnWindowFocus: 'always' });
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
      owner.sequence++;
      const queryKey = ['workflow-sequential', slug, 'v2', kind, id];
      void qc.cancelQueries({ queryKey, exact: true });
      qc.removeQueries({ queryKey, exact: true });
    };
  }, [qc, owner, slug, kind, id]);
  return { ...query, data: query.isFetchedAfterMount && query.data?.org_slug === slug ? query.data : undefined };
}

export const useSequentialRunsV2 = () => useSequentialProjection('runs', 'all', listSequentialRunsV2);
export const useSequentialRunV2 = (id: string) => useSequentialProjection('run', id, (slug, signal) => getSequentialRunV2(slug, id, signal));
export const useTaskSequentialWorkflows = (id: string) => useSequentialProjection('task', id, (slug, signal) => getTaskSequentialWorkflows(slug, id, signal));
