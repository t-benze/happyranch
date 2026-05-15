/**
 * Mock agent roster — stub for the Agents page (PR 6+).
 * Currently empty; ThreadsPage doesn't consume it. Kept here so the mocks
 * barrel doesn't have to change when later features land.
 */
export const MOCK_AGENTS: { name: string; team: string; role: 'manager' | 'worker' }[] = [];
