import { describe, expect, it } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { server } from '@/test/server';
import { I18nProvider } from '@/hooks/i18n';
import { savedLocaleAdapter } from '@/test/render';
import { translate, type MessageKey, type MessageParams } from '@/lib/i18n';
import type { AgentSummary, KBEntry, OrgsListResponse, TaskRecord, ThreadRecord } from '@/lib/api/types';
import { buildSections, CommandPaletteHost } from './CommandPaletteHost';

function setup({
  route,
  seed,
  locale = 'en' as const,
}: {
  route: string;
  seed?: (qc: QueryClient) => void;
  locale?: 'en' | 'zh-CN';
}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  seed?.(qc);

  let httpHits = 0;
  server.events.removeAllListeners();
  server.events.on('request:start', () => {
    httpHits += 1;
  });

  const utils = render(
    <I18nProvider adapter={savedLocaleAdapter(locale)} mode="preview">
      <MemoryRouter initialEntries={[route]}>
        <QueryClientProvider client={qc}>
          <Routes>
            <Route path="/orgs/:slug/*" element={<CommandPaletteHost />} />
            <Route path="/" element={<CommandPaletteHost />} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>
    </I18nProvider>,
  );

  return { qc, utils, getHttpHits: () => httpHits };
}

/** Seed the exact cache shapes `buildSections` reads, with authored content. */
function seedAuthored(qc: QueryClient): void {
  qc.setQueryData<OrgsListResponse>(['orgs'], {
    orgs: [{ slug: 'hk-macau-tourism', root: '/x' }],
  } as OrgsListResponse);
  qc.setQueryData(['threads', 'hk-macau-tourism'], {
    threads: [
      { thread_id: 'THR-42', subject: '签证指南 review' } as unknown as ThreadRecord,
    ],
  });
  qc.setQueryData(['tasks', 'hk-macau-tourism'], {
    tasks: [
      { task_id: 'TASK-9', brief: '刷新酒店列表' } as unknown as TaskRecord,
    ],
  });
  qc.setQueryData(['agents', 'hk-macau-tourism'], {
    agents: [{ name: 'dev_agent', team: 'engineering' } as AgentSummary],
  });
  qc.setQueryData(['kb-list', 'hk-macau-tourism'], {
    entries: [{ slug: 'hk-visa-rules', title: '香港签证规则' } as KBEntry],
  });
}

function fireCmdK() {
  fireEvent.keyDown(window, { key: 'k', metaKey: true });
}

describe('CommandPaletteHost', () => {
  it('mounts inert — no fetch on render', async () => {
    const { getHttpHits } = setup({ route: '/orgs/demo/threads' });
    // Give microtasks a chance.
    await Promise.resolve();
    expect(getHttpHits()).toBe(0);
  });

  // ⌘K hotkey was moved to AssistantDockHost (design-overhaul v1).
  // The command palette no longer responds to Cmd-K; it is opened
  // via programmatic control when needed. W2a does NOT reactivate it.
  it('stays closed on Cmd-K (hotkey moved to AssistantDock)', () => {
    setup({ route: '/orgs/demo/threads' });
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => fireCmdK());
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders nothing-loaded state when opened programmatically', () => {
    const { qc } = setup({ route: '/orgs/demo/threads' });
    act(() => {
      qc.clear();
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('localizes section labels while authored values and hrefs stay byte-exact (case D)', () => {
    const qc = new QueryClient();
    seedAuthored(qc);
    const zh = (key: MessageKey, params?: MessageParams) => translate('zh-CN', key, params);
    const sections = buildSections(qc, 'hk-macau-tourism', zh);
    const byLabel = new Map(sections.map((s) => [s.label, s]));
    expect([...byLabel.keys()]).toEqual(['会话', '任务', '智能体', '知识库', '组织']);
    expect(byLabel.get('会话')?.items[0]).toMatchObject({
      key: 'thread:THR-42',
      primary: 'THR-42 · 签证指南 review',
      href: '/orgs/hk-macau-tourism/threads/THR-42',
    });
    expect(byLabel.get('任务')?.items[0].primary).toBe('TASK-9 · 刷新酒店列表');
    expect(byLabel.get('组织')?.items[0]).toMatchObject({
      primary: 'hk-macau-tourism',
      href: '/orgs/hk-macau-tourism/threads',
    });
  });

  it('uses English section labels under the en locale', () => {
    const qc = new QueryClient();
    seedAuthored(qc);
    const sections = buildSections(qc, 'hk-macau-tourism', (key) => translate('en', key));
    expect(sections.map((s) => s.label)).toEqual(['Threads', 'Tasks', 'Agents', 'KB', 'Orgs']);
  });
});
