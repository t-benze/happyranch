import { useEffect, useState, type ReactNode } from 'react';
import { useOrgSlugOptional } from '@/lib/orgSlug';
import { useNavigate } from 'react-router-dom';
import type { Meta, StoryObj } from '@storybook/react';
import { PrototypeProvider } from './providers/PrototypeProvider';
import { DataContext, useData, type TaskListStream, type TasksApi } from './providers/DataContext';
import { mockTasksApi, staticTaskListStreams } from './providers/_mock-tasks';
import { AppBar } from './layouts/AppShell/AppBar';
import type { TaskPauseProjection, TaskRecord } from '@/lib/api/types';
import { TasksPage } from '@/features/tasks/TasksPage';

type State = 'populated' | 'loading' | 'empty' | 'error' | 'long' | 'no-escalated' | 'pause-pausing' | 'pause-paused' | 'pause-unavailable';
function Fixture({ state, children }: { state: State; children: ReactNode }) {
  const data = useData();
  const org = useOrgSlugOptional();
  const navigate = useNavigate();
  useEffect(() => { navigate('/orgs/demo-org/tasks', { replace: true }); }, [navigate]);
  const [failed, setFailed] = useState(state === 'error');
  const roots = (): TaskRecord[] => {
    const base = mockTasksApi.useTasksRootsInfinite().data!.pages[0].tasks;
    if (state === 'empty' || state === 'loading') return [];
    if (state.startsWith('pause-')) {
      const paused = state === 'pause-paused';
      const pause: TaskPauseProjection = { org_slug: org ?? 'prototype', root_task_id: 'TASK-PAUSE-ROOT', lifecycle_status: 'in_progress',
        held: true, effective_hold: true, generation: 3, control_state: paused ? 'paused' : 'pausing',
        paused_at: '2026-10-11T00:00:00Z', resumed_at: null, observed_at: '2026-10-11T00:00:00Z',
        evidence_complete: paused, blockers: paused ? [] : [{ kind: 'unknown', task_id: 'TASK-PAUSE-ROOT', job_id: 'JOB-PAUSE', owner_kind: 'host', reason: 'tree_evidence_unavailable', observed_at: '2026-10-11T00:00:00Z' }] };
      return [{ ...base[0], task_id: pause.root_task_id, status: 'in_progress' as const, severity_rollup: 'in_progress', block_kind: 'delegated' as const,
        pause: state === 'pause-unavailable' ? undefined : pause }];
    }
    if (state === 'no-escalated') return base.filter((task) => task.status !== 'escalated');
    if (state !== 'long') return base;
    const statuses = ['escalated', 'in_progress', 'pending', 'completed', 'failed', 'cancelled', 'superseded'] as const;
    return statuses.map((status, i) => ({ ...base[0], task_id: `TASK-LONG-IDENTIFIER-${i}`, status, severity_rollup: status,
      brief: `# ${'Long unbroken headline'.repeat(8)}\nPreserved detail text`, assigned_agent: 'long_exact_agent_name', dispatched_from_thread_id: 'THR-LONG-IDENTIFIER',
    }));
  };
  const useFixtureStreams: TasksApi['useTasksListStreams'] = (filters) => {
    const streams = staticTaskListStreams(roots(), filters);
    const retry = async () => { setFailed(false); };
    const ordinary = streams.ordinary.map((stream): TaskListStream => (
      state === 'loading' ? { ...stream, isInitialPending: true }
        : failed ? { ...stream, tasks: [], isInitialError: true, retry, refresh: retry }
          : stream));
    return {
      ordinary,
      attention: state === 'loading' ? { ...streams.attention, isInitialPending: true } : streams.attention,
    };
  };
  return <DataContext.Provider value={{ ...data, tasks: { ...data.tasks, useTasksListStreams: useFixtureStreams } }}>{children}</DataContext.Provider>;
}
const meta = {
  title: 'Design System/Tasks/List', component: TasksPage,
  decorators: [(Story, context) => <PrototypeProvider key={context.parameters.taskState ?? 'populated'}><Fixture state={(context.parameters.taskState ?? 'populated') as State}>
    <div className="flex h-screen flex-col"><AppBar presentation="tasks" /><Story /></div>
  </Fixture></PrototypeProvider>],
} satisfies Meta<typeof TasksPage>;
export default meta;
type Story = StoryObj<typeof meta>;
/** Local fixtures only. Retry is a presentation example, not real-provider proof.
 *  The populated fixture carries an escalated root: its independent
 *  status=escalated traversal renders 'Waiting on you' as the FIRST
 *  ordinary-styled group inside the shared list shell. */
export const Populated: Story = {};
export const Loading: Story = { parameters: { taskState: 'loading' } };
export const Empty: Story = { parameters: { taskState: 'empty' } };
export const InitialErrorRetry: Story = { parameters: { taskState: 'error' } };

export const LongContent: Story = { parameters: { taskState: 'long' } };

/** No escalated roots: no 'Waiting on you' group, heading or empty card. */
export const NoEscalated: Story = { parameters: { taskState: 'no-escalated' } };

/** UI fixtures only: prototype control submissions refuse as unavailable, never simulate enforcement. */
export const PausingRoot: Story = { parameters: { taskState: 'pause-pausing' } };
export const PausedRoot: Story = { parameters: { taskState: 'pause-paused' } };
export const PauseProjectionUnavailable: Story = { parameters: { taskState: 'pause-unavailable' } };
