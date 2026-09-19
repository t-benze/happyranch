import { useEffect, useRef, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useInfiniteQuery } from '@tanstack/react-query';
import type { Meta, StoryObj } from '@storybook/react';
import { PrototypeProvider } from './providers/PrototypeProvider';
import { DataContext, useData, type TasksApi } from './providers/DataContext';
import { mockTasksApi } from './providers/_mock-tasks';
import { AppBar } from './layouts/AppShell/AppBar';
import { TasksPage } from '@/features/tasks/TasksPage';

type State = 'populated' | 'loading' | 'empty' | 'error' | 'long' | 'no-escalated';
function Fixture({ state, children }: { state: State; children: ReactNode }) {
  const data = useData();
  const navigate = useNavigate();
  useEffect(() => { navigate('/orgs/demo-org/tasks', { replace: true }); }, [navigate]);
  const attempts = useRef(0);
  const useFixtureRoots: TasksApi['useTasksRootsInfinite'] = (params) => {
    const query = useInfiniteQuery({
      queryKey: ['tasks-roots-infinite', 'demo-org', params],
      initialPageParam: undefined,
      queryFn: async () => {
        if (state === 'error' && attempts.current++ === 0) throw new Error('Synthetic story error');
        const base = mockTasksApi.useTasksRootsInfinite(params).data!.pages[0];
        if (state === 'no-escalated') {
          return { tasks: base.tasks.filter((task) => task.status !== 'escalated'), next_cursor: null };
        }
        if (state !== 'long') return base;
        const root = mockTasksApi.useTasksRootsInfinite().data!.pages[0].tasks[0];
        const statuses = ['escalated', 'in_progress', 'pending', 'completed', 'failed', 'cancelled', 'superseded'] as const;
        return { tasks: statuses.map((status, i) => ({ ...root, task_id: `TASK-LONG-IDENTIFIER-${i}`, status, severity_rollup: status,
          brief: `# ${'Long unbroken headline'.repeat(8)}\nPreserved detail text`, assigned_agent: 'long_exact_agent_name', dispatched_from_thread_id: 'THR-LONG-IDENTIFIER',
        })).filter((task) => (!params?.status || task.status === params.status) && (!params?.assigned_agent || task.assigned_agent === params.assigned_agent)), next_cursor: null };

      },
      getNextPageParam: () => undefined,
      enabled: state !== 'loading', retry: false,
    });
    return {
      data: state === 'empty' ? { pages: [{ tasks: [], next_cursor: null }] } : query.data,
      isLoading: state === 'loading' || query.isLoading,
      isError: query.isError, error: query.error,
      hasNextPage: false, isFetchingNextPage: false, fetchNextPage: () => query.fetchNextPage(),
    };
  };
  return <DataContext.Provider value={{ ...data, tasks: { ...data.tasks, useTasksRootsInfinite: useFixtureRoots } }}>{children}</DataContext.Provider>;
}
const meta = {
  title: 'Design System/Tasks/List', component: TasksPage,
  decorators: [(Story, context) => <PrototypeProvider key={context.parameters.taskState ?? 'populated'}><Fixture state={(context.parameters.taskState ?? 'populated') as State}>
    <div className="flex h-screen flex-col"><AppBar presentation="tasks" showAssistantControl={false} /><Story /></div>
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
