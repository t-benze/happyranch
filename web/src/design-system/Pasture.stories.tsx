import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { Button } from './primitives/Button';
import { Input } from './primitives/Input';
import { Label } from './primitives/Label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './primitives/Select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './primitives/Tabs';
import { Textarea } from './primitives/Textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from './primitives/Tooltip';
import { AgentChip } from './patterns/AgentChip';
import { EmptyState } from './patterns/EmptyState';
import { FormField } from './patterns/FormField';
import { IdBadge } from './patterns/IdBadge';
import { KbdChip } from './patterns/KbdChip';
import { Markdown } from './patterns/Markdown';
import { Sparkline } from './patterns/Sparkline';
import { StatValue } from './patterns/StatValue';
import { StatusBadge } from './patterns/StatusBadge';

const meta = {
  title: 'Pasture/Overview',
  tags: ['autodocs'],
  parameters: {
    docs: { description: { component: 'The rendered Pasture component-library specification, expressed with shipped semantic tokens and reusable components. Stories are local and daemon-independent.' } },
  },
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

const Section = ({ eyebrow, title, children }: { eyebrow: string; title: string; children: React.ReactNode }) => (
  <section className="mx-auto grid w-full max-w-content gap-5 border-b border-border-subtle py-8 first:pt-0 last:border-0">
    <header><p className="text-caption font-semibold uppercase tracking-wide text-accent-text">{eyebrow}</p><h2 className="mt-1 font-display text-display text-text-primary">{title}</h2></header>
    {children}
  </section>
);
const Card = ({ children }: { children: React.ReactNode }) => <div className="rounded-lg border border-border-default bg-surface-raised p-5 shadow-sm">{children}</div>;

export const Foundations: Story = { render: () => <div>
  <Section eyebrow="Foundations" title="Tokens"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[
    ['Canvas', 'bg-surface-canvas'], ['Raised', 'bg-surface-raised'], ['Sunken', 'bg-surface-sunken'], ['Overlay', 'bg-surface-overlay'],
    ['Accent', 'bg-accent-default'], ['Success', 'bg-feedback-success'], ['Warning', 'bg-feedback-warning'], ['Danger', 'bg-feedback-danger'],
  ].map(([label, tone]) => <div key={label} className={`min-h-24 rounded-lg border border-border-default p-4 ${tone}`}><span className="text-caption font-semibold">{label}</span></div>)}</div></Section>
  <Section eyebrow="Foundations" title="Radius, shadow & layout"><div className="grid gap-4 sm:grid-cols-3"><div className="rounded border border-border-default bg-surface-raised p-5">Small radius</div><div className="rounded-lg border border-border-default bg-surface-raised p-5 shadow-sm">Card radius</div><div className="rounded-full border border-border-default bg-surface-raised p-5 text-center">Pill radius</div></div></Section>
</div> };

export const IdentityAndTypography: Story = { render: () => <div>
  <Section eyebrow="Foundations" title="Agent identity ramp"><div className="flex flex-wrap gap-3"><AgentChip name="founder" role="founder" /><AgentChip name="engineering_manager" role="manager" /><AgentChip name="frontend_engineer" role="worker" /></div></Section>
  <Section eyebrow="Foundations" title="Typography"><Card><p className="font-display text-display">Newsreader display, 30px</p><h3 className="mt-4 text-h2">Section heading</h3><p className="mt-2 text-body text-text-secondary">Hanken Grotesk carries dense interface copy clearly.</p><code className="mt-3 block text-mono-sm text-accent-text">TASK-6622 · JetBrains Mono</code></Card></Section>
</div> };

export const FocusAndButtons: Story = { render: () => <Section eyebrow="Components" title="Focus, disabled & buttons"><div className="grid gap-5"><div className="flex flex-wrap gap-3">{(['default', 'secondary', 'outline', 'ghost', 'destructive', 'link'] as const).map((variant) => <Button key={variant} variant={variant}>{variant}</Button>)}</div><div className="flex flex-wrap gap-3"><Button size="sm">Small</Button><Button>Default</Button><Button size="lg">Large</Button><Button disabled>Disabled</Button></div><p className="text-caption text-text-muted">Use Tab to inspect the token-backed focus-visible ring.</p></div></Section> };

export const CardsBadgesAndChips: Story = { render: () => <div>
  <Section eyebrow="Components" title="Cards"><div className="grid gap-4 md:grid-cols-2"><Card><h3 className="text-h2">Default card</h3><p className="mt-2 text-body text-text-secondary">Raised surface, quiet border, compact shadow.</p></Card><Card><h3 className="text-h2">Interactive card</h3><p className="mt-2 text-body text-text-secondary">Content density stays readable at narrow widths.</p></Card></div></Section>
  <Section eyebrow="Components" title="Badges, tags, chips & roll-ups"><div className="flex flex-wrap items-center gap-3"><StatusBadge status="open" /><StatusBadge status="in_progress" /><StatusBadge status="blocked" /><StatusBadge status="failed" /><IdBadge id="THR-221" kind="thread" /><IdBadge id="TASK-6622" kind="task" /><KbdChip keys={['Ctrl', 'Enter']} /></div></Section>
</div> };

export const FormControls: Story = { render: () => <Section eyebrow="Components" title="Form controls & selection"><div className="grid max-w-2xl gap-5 sm:grid-cols-2"><FormField label="Name" htmlFor="pasture-name"><Input id="pasture-name" placeholder="Pasture" /></FormField><FormField label="Validation" htmlFor="pasture-error" error="A name is required"><Input id="pasture-error" aria-invalid /></FormField><div className="grid gap-2"><Label htmlFor="pasture-notes">Notes</Label><Textarea id="pasture-notes" placeholder="Add a note…" /></div><div className="grid content-start gap-2"><Label>Status</Label><Select defaultValue="ready"><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ready">Ready</SelectItem><SelectItem value="blocked">Blocked</SelectItem></SelectContent></Select></div></div></Section> };

export const OverlaysAndRecovery: Story = { render: () => <div>
  <Section eyebrow="Components" title="Callout, confirm & recovery"><div className="grid gap-3"><div className="rounded-lg border border-feedback-info bg-info-soft p-4 text-body">Information stays calm and actionable.</div><div className="rounded-lg border border-feedback-warning bg-attention-soft p-4 text-body">Confirm consequential actions before continuing.</div><div className="flex items-center justify-between gap-3 rounded-lg border border-feedback-danger bg-danger-soft p-4"><span>Error details remain visible.</span><Button variant="outline" size="sm">Retry</Button></div></div></Section>
  <Section eyebrow="Components" title="Popover, action bar & tooltip"><div className="flex flex-wrap items-center gap-3"><Tooltip defaultOpen><TooltipTrigger asChild><Button variant="ghost">Hover or focus</Button></TooltipTrigger><TooltipContent>Helpful, concise context</TooltipContent></Tooltip><div className="flex rounded-lg border border-border-default bg-surface-raised p-2 shadow-sm"><Button size="sm">Save</Button><Button size="sm" variant="ghost">Cancel</Button></div></div></Section>
</div> };

export const StatsTimelineAndTable: Story = { render: () => <div>
  <Section eyebrow="Data display" title="Stats & meters"><div className="grid gap-4 sm:grid-cols-3"><Card><p className="text-caption text-text-muted">Tokens</p><StatValue value={3707054} format="tokens" /></Card><Card><p className="text-caption text-text-muted">Tasks</p><StatValue value={42} format="count" /></Card><Card><p className="text-caption text-text-muted">Utilization</p><div className="mt-3 h-2 overflow-hidden rounded-full bg-surface-sunken"><div className="h-full w-2/3 bg-accent-default" /></div></Card></div></Section>
  <Section eyebrow="Data display" title="Timeline & tables"><div className="overflow-x-auto rounded-lg border border-border-default bg-surface-raised"><table className="w-full min-w-lg text-left text-body"><thead className="bg-surface-sunken text-caption uppercase text-text-muted"><tr><th className="p-3">Event</th><th className="p-3">Owner</th><th className="p-3">State</th></tr></thead><tbody><tr className="border-t border-border-subtle"><td className="p-3">Reference rendered</td><td className="p-3">frontend_engineer</td><td className="p-3"><StatusBadge status="completed" /></td></tr><tr className="border-t border-border-subtle"><td className="p-3">Visual comparison</td><td className="p-3">TASK-6622</td><td className="p-3"><StatusBadge status="in_progress" /></td></tr></tbody></table></div></Section>
</div> };

export const ProseStatusAndProvenance: Story = { render: () => <div>
  <Section eyebrow="Content" title="Prose, properties & code"><Card><Markdown body={'### What the skill does\n\n- Preserves semantic tokens\n- Keeps examples local\n\n```ts\nnpm run build-storybook\n```'} /></Card></Section>
  <Section eyebrow="Content" title="Status, provenance & readiness"><div className="grid gap-3 sm:grid-cols-2"><Card><dl className="grid grid-cols-2 gap-2 text-body"><dt className="text-text-muted">Source</dt><dd>Founder reference</dd><dt className="text-text-muted">Task</dt><dd><IdBadge id="TASK-6622" kind="task" /></dd></dl></Card><Card><p className="text-caption text-text-muted">Validation readiness</p><div className="mt-3"><Sparkline data={[0.2, 0.4, 0.35, 0.7, 0.9]} variant="green" /></div></Card></div></Section>
</div> };

function StatefulPageStates(): JSX.Element { const [loading, setLoading] = useState(false); return <Section eyebrow="Patterns" title="Page states"><Tabs defaultValue="empty"><TabsList variant="underline" className="h-auto flex-wrap">{['empty', 'loading', 'error', 'unauthorized', 'populated'].map((state) => <TabsTrigger key={state} value={state} variant="underline" className="whitespace-normal">{state[0].toUpperCase() + state.slice(1)}</TabsTrigger>)}</TabsList><TabsContent value="empty"><div className="h-64"><EmptyState title="Nothing here yet" body="Create the first item when you are ready." cta={{ label: 'Create item', onClick: () => undefined }} /></div></TabsContent><TabsContent value="loading"><Card><div className="grid animate-pulse gap-3"><div className="h-4 w-1/3 rounded bg-surface-subtle"/><div className="h-16 rounded bg-surface-subtle"/></div></Card></TabsContent><TabsContent value="error"><div className="rounded-lg border border-feedback-danger bg-danger-soft p-5"><h3 className="text-h2">Could not load this view</h3><Button className="mt-4" variant="outline" onClick={() => setLoading(!loading)}>{loading ? 'Retrying…' : 'Retry'}</Button></div></TabsContent><TabsContent value="unauthorized"><div className="rounded-lg border border-border-default bg-surface-raised p-5"><h3 className="text-h2">Permission required</h3><p className="mt-2 text-body text-text-secondary">This proposal demonstrates copy only; auth remains product-owned.</p></div></TabsContent><TabsContent value="populated"><Card><h3 className="text-h2">Three active items</h3><p className="mt-2 text-body text-text-secondary">Representative populated content.</p></Card></TabsContent></Tabs></Section>; }
export const PageStates: Story = { render: () => <StatefulPageStates /> };

export const AssistantDockAndDevAffordance: Story = { render: () => <div>
  <Section eyebrow="Patterns" title="Assistant dock"><div className="ml-auto max-w-md rounded-lg border border-border-default bg-surface-raised p-4 shadow-lg"><p className="text-caption font-semibold uppercase text-accent-text">Ranch assistant</p><p className="mt-2 text-body">How can I help with this catalogue?</p><div className="mt-4 flex gap-2"><Input aria-label="Ask the assistant" placeholder="Ask a question…" /><Button>Send</Button></div></div></Section>
  <Section eyebrow="Reference notes" title="Dev affordance, rename map & screen-scoped"><Card><p className="text-body text-text-secondary">Implementation names remain stable. Reference-only proposals are documented without manufacturing new production primitives or changing screen-owned behavior.</p></Card></Section>
</div> };
