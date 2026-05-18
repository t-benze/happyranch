/**
 * Design-system route — `/__design__`.
 *
 * Renders every entry from `registry.json` grouped by layer (primitive →
 * pattern → layout). Each row shows the component name, its `consumes`
 * token-block names, the `example` code string, and a live render. Source of
 * truth for the AI designer agent.
 *
 * Mount point: see `routes.tsx`. Production gating mirrors `/__prototypes`
 * via `DESIGN_ROUTE_DISABLED`; flip `VITE_ENABLE_DESIGN_ROUTE=true` to ship
 * it.
 *
 * The live `componentMap` covers every entry in the registry. Components
 * with portal-backed open state (Dialog, HelpSheet) render `open={false}`
 * so the route stays scrollable. The dispatch is keyed by the `name` in the
 * registry, so adding a new component requires both `meta` + a `componentMap`
 * entry — the type system will not catch a missing entry, but the registry
 * length check at render time will.
 */
import type { ReactNode } from 'react';
import { Link, Outlet, Route } from 'react-router-dom';
import { PrototypeProvider } from '@/design-system/providers/PrototypeProvider';
import { TopBar } from '@/design-system/layouts/AppShell/TopBar';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { Composer } from '@/design-system/patterns/Composer';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { FormField } from '@/design-system/patterns/FormField';
import { HelpSheet } from '@/design-system/patterns/HelpSheet';
import { IdBadge } from '@/design-system/patterns/IdBadge';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import { KbdChip } from '@/design-system/patterns/KbdChip';
import { MessageBubble } from '@/design-system/patterns/MessageBubble';
import { PageHeader } from '@/design-system/patterns/PageHeader';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { ThreadHeader } from '@/design-system/patterns/ThreadHeader';
import { TierBadge } from '@/design-system/patterns/TierBadge';
import registry from '@/design-system/registry.json';

export const DESIGN_ROUTE_DISABLED =
  import.meta.env.PROD && !import.meta.env.VITE_ENABLE_DESIGN_ROUTE;

interface RegistryEntry {
  name: string;
  layer: 'primitive' | 'pattern' | 'layout';
  import: string;
  variants: Record<string, readonly string[]>;
  consumes: readonly string[];
  example: string;
}

const ENTRIES = registry.components as unknown as readonly RegistryEntry[];

/**
 * Live-render dispatch. Keys must match the `name` field in `registry.json`.
 * Each entry renders one representative invocation with safe stub props
 * (`() => {}` for handlers, `open={false}` for portal dialogs).
 */
const componentMap: Record<string, ReactNode> = {
  Button: <Button>Send</Button>,
  Dialog: (
    <Dialog open={false}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirm</DialogTitle>
          <DialogDescription>Renders into a portal when open.</DialogDescription>
        </DialogHeader>
      </DialogContent>
    </Dialog>
  ),
  AgentChip: <AgentChip name="engineering_head" role="manager" />,
  IdBadge: <IdBadge id="THR-042" kind="thread" />,
  KbdChip: <KbdChip keys={['Ctrl', 'Enter']} />,
  StatusBadge: <StatusBadge status="open" />,
  TierBadge: <TierBadge tier="green" />,
  EmptyState: (
    <div className="border-border-default h-40 w-full max-w-md border">
      <EmptyState
        title="No threads"
        body="Start one with grassland threads compose."
      />
    </div>
  ),
  PageHeader: <PageHeader title="Threads" meta="12 open" />,
  FormField: (
    <div className="w-72">
      <FormField label="Subject" htmlFor="design-subject">
        <input
          id="design-subject"
          placeholder="Refund policy"
          className="border-border-default bg-surface-raised text-body text-text-primary placeholder:text-text-muted rounded-md border px-2 py-1"
        />
      </FormField>
    </div>
  ),
  Composer: (
    <div className="w-[28rem]">
      <Composer
        onSend={() => {}}
        helper="Ctrl+Enter to send (preview, won't fire)"
      />
    </div>
  ),
  HelpSheet: (
    <HelpSheet
      open={false}
      onClose={() => {}}
      shortcuts={[
        { keys: ['?'], description: 'Open this help sheet' },
        { keys: ['R'], description: 'Focus the composer' },
      ]}
    />
  ),
  InboxRow: (
    <div className="w-[20rem]">
      <InboxRow
        threadId="THR-042"
        subject="Refund policy review"
        lastSpeaker={{ name: 'compliance_head', role: 'manager' }}
        meta="2m"
        status="open"
        needsYou
        active={false}
        href="#"
        onSelect={() => {}}
      />
    </div>
  ),
  MessageBubble: (
    <MessageBubble
      variant="worker"
      seq={1}
      speaker="content_writer"
      speakerRole="worker"
      timestamp="2026-05-15T10:00:00Z"
      body="Drafted the blog post. Ready for review."
    />
  ),
  ThreadHeader: (
    <ThreadHeader
      threadId="THR-042"
      subject="Refund policy review"
      status="open"
      participants={['founder', 'compliance_head']}
      turnsUsed={3}
      turnCap={20}
    />
  ),
  TopBar: <TopBar />,
};

export function designRoutes(): JSX.Element {
  return (
    <Route path="/__design__" element={<DesignLayout />}>
      <Route index element={<DesignIndex />} />
    </Route>
  );
}

function DesignLayout(): JSX.Element {
  return (
    <PrototypeProvider>
      <div className="flex h-full flex-col">
        <DesignBanner />
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </PrototypeProvider>
  );
}

function DesignBanner(): JSX.Element {
  return (
    <div className="border-border-default bg-accent-muted text-caption text-text-secondary flex shrink-0 items-center gap-3 border-b px-4 py-1">
      <span className="text-text-primary font-semibold">Design system</span>
      <span>—</span>
      <span>Live registry render. Read-only.</span>
      <span className="ml-auto">
        <Link to="/" className="text-accent-default hover:underline">
          Exit
        </Link>
      </span>
    </div>
  );
}

function DesignIndex(): JSX.Element {
  const groups: Array<{ heading: string; layer: RegistryEntry['layer'] }> = [
    { heading: 'Primitives', layer: 'primitive' },
    { heading: 'Patterns', layer: 'pattern' },
    { heading: 'Layouts', layer: 'layout' },
  ];

  return (
    <div className="text-text-primary mx-auto max-w-5xl p-8">
      <header className="mb-6">
        <h1 className="text-h1">Design system</h1>
        <p className="text-body text-text-secondary mt-2">
          {ENTRIES.length} components from{' '}
          <code className="text-text-primary font-mono">registry.json</code>.
          Live-rendered. Source of truth for the AI designer agent.
        </p>
      </header>

      {groups.map((group) => {
        const items = ENTRIES.filter((e) => e.layer === group.layer);
        if (items.length === 0) return null;
        return (
          <section key={group.layer} className="mb-10">
            <h2 className="text-h2 mb-4">
              {group.heading}
              <span className="text-caption text-text-muted ml-2">
                ({items.length})
              </span>
            </h2>
            <div className="flex flex-col gap-6">
              {items.map((entry) => (
                <EntryCard key={entry.name} entry={entry} />
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function EntryCard({ entry }: { entry: RegistryEntry }): JSX.Element {
  const live = componentMap[entry.name];
  return (
    <article className="border-border-default bg-surface-raised rounded-lg border p-4">
      <header className="mb-3 flex flex-wrap items-baseline gap-3">
        <h3 className="text-h3 text-text-primary">{entry.name}</h3>
        <code className="text-caption text-text-muted font-mono">
          {entry.import}
        </code>
        <div className="ml-auto flex flex-wrap items-center gap-1">
          {entry.consumes.map((token) => (
            <span
              key={token}
              className="border-border-subtle bg-surface-sunken text-mono-sm text-id-task rounded-sm border px-2 py-px font-mono"
            >
              {token}
            </span>
          ))}
        </div>
      </header>

      {Object.keys(entry.variants).length > 0 && (
        <div className="text-caption text-text-muted mb-3 flex flex-wrap items-center gap-3">
          <span className="text-text-secondary font-semibold">variants</span>
          {Object.entries(entry.variants).map(([prop, values]) => (
            <span key={prop} className="inline-flex items-center gap-1">
              <code className="text-text-primary font-mono">{prop}</code>
              <span>=</span>
              <code className="font-mono">[{values.join(', ')}]</code>
            </span>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <pre className="border-border-subtle bg-surface-sunken text-mono-sm text-text-secondary overflow-x-auto rounded-md border p-3 font-mono">
          {entry.example}
        </pre>
        <div className="border-border-subtle bg-surface-sunken flex items-center justify-center rounded-md border border-dashed p-3">
          {live ?? (
            <span className="text-caption text-feedback-danger">
              No live render registered for {entry.name}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}
