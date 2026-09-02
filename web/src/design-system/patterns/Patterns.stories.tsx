import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react';
import { Button } from '../primitives/Button';
import { AgentChip } from './AgentChip';
import { CommandPalette } from './CommandPalette';
import { Composer } from './Composer';
import { CrescentMoonBadge } from './CrescentMoonBadge';
import { EmptyState } from './EmptyState';
import { FilterSidebar } from './FilterSidebar';
import { FormField } from './FormField';
import { HelpSheet } from './HelpSheet';
import { IdBadge } from './IdBadge';
import { InboxRow } from './InboxRow';
import { KbdChip } from './KbdChip';
import { Markdown } from './Markdown';
import { MentionAutocomplete } from './MentionAutocomplete';
import { MentionTextarea } from './MentionTextarea';
import Mermaid from './Mermaid';
import { MessageBubble } from './MessageBubble';
import { PageHeader } from './PageHeader';
import { RecipientsInput } from './RecipientsInput';
import { Sparkline } from './Sparkline';
import { StatValue } from './StatValue';
import { StatusBadge } from './StatusBadge';
import { ThreadHeader } from './ThreadHeader';
import { TypingBubble } from './TypingBubble';
import type { AgentSummary } from '@/lib/api/types';

const meta = { title: 'Design System/Patterns', tags: ['autodocs'], parameters: { docs: { description: { component: 'Reusable product patterns shown with representative empty, populated, error, loading, and interaction states where those states apply.' } } } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

const agents: AgentSummary[] = [
  { name: 'engineering_manager', team: 'engineering', role: 'manager', executor: 'codex', description: 'Engineering lead', repos: {}, system_prompt: '' },
  { name: 'frontend_engineer', team: 'engineering', role: 'worker', executor: 'codex', description: 'Frontend', repos: {}, system_prompt: '' },
];

export const AgentChipRoles: Story = { render: () => <div className="flex flex-wrap gap-3"><AgentChip name="founder" role="founder" /><AgentChip name="engineering_manager" role="manager" /><AgentChip name="frontend_engineer" role="worker" /></div> };
export const CommandPalettePopulated: Story = { render: () => <CommandPalette open onClose={() => undefined} onSelect={() => undefined} sections={[{ label: 'Tasks', items: [{ key: 'task:1', primary: 'Replace catalogue with Storybook', secondary: 'TASK-6530', href: '#' }] }]} /> };
export const ComposerStates: Story = { render: () => <div className="grid max-w-2xl gap-6"><Composer agents={agents} threadId="THR-STORY" orgSlug="storybook" onSend={() => undefined} helper="Write a local draft…" abortReplies={{ active: true, isPending: false, onAbort: () => undefined }} /><Composer agents={agents} threadId="THR-ERROR" orgSlug="storybook" onSend={() => undefined} errorMessage="Message could not be sent; your draft is preserved." /></div> };
export const CrescentMoonBadgeState: Story = { render: () => <CrescentMoonBadge /> };
export const EmptyStateWithAction: Story = { render: () => <div className="h-64 max-w-xl"><EmptyState title="No threads" body="Start a focused conversation with your team." cta={{ label: 'New thread', onClick: () => undefined }} /></div> };

function FilterExample(): JSX.Element { const [value, setValue] = useState<Record<string, string | null>>({ status: null }); return <FilterSidebar groups={[{ key: 'status', label: 'Status', options: [{ value: 'open', label: 'Open', count: 12 }, { value: 'archived', label: 'Archived', count: 3 }] }]} value={value} onChange={setValue} />; }
export const FilterSidebarInteraction: Story = { render: () => <FilterExample /> };
export const FormFieldStates: Story = { render: () => <div className="grid max-w-sm gap-5"><FormField label="Subject" htmlFor="subject"><input id="subject" className="input" /></FormField><FormField label="Executor" htmlFor="executor" error="Executor is required"><input id="executor" className="input" aria-invalid /></FormField></div> };
export const HelpSheetInteraction: Story = { render: () => <HelpSheet open onClose={() => undefined} shortcuts={[{ keys: ['?'], description: 'Open help' }, { keys: ['Ctrl', 'Enter'], description: 'Send message' }]} /> };
export const IdBadgeKinds: Story = { render: () => <div className="flex gap-3"><IdBadge id="THR-042" kind="thread" /><IdBadge id="TASK-6530" kind="task" /></div> };
export const InboxRowStates: Story = { render: () => <div className="grid max-w-2xl gap-2"><InboxRow threadId="THR-042" subject="Refund policy review" lastSpeaker={{ name: 'engineering_manager', role: 'manager' }} meta="2m" status="open" needsYou active href="#" /><InboxRow threadId="THR-043" subject="Archived decision" status="archived" needsYou={false} active={false} href="#" layout="thread" /></div> };
export const KbdChipCombinations: Story = { render: () => <div className="flex gap-3"><KbdChip keys={['?']} /><KbdChip keys={['Ctrl', 'Enter']} /></div> };
export const MarkdownContent: Story = { render: () => <Markdown body={'## Decision\n\n- Token-backed styles\n- **No live daemon**\n\n```ts\nnpm run storybook\n```'} /> };
export const MentionAutocompletePopulated: Story = { render: () => <MentionAutocomplete anchor={{ x: 32, y: 360, width: 280, height: 40 }} matches={agents} onSelect={() => undefined} onDismiss={() => undefined} /> };

function MentionExample(): JSX.Element { const [value, setValue] = useState('@front'); return <div className="max-w-xl"><MentionTextarea value={value} onChange={setValue} agents={agents} ariaLabel="Message with mentions" /></div>; }
export const MentionTextareaInteraction: Story = { render: () => <MentionExample /> };
export const MermaidDiagram: Story = { render: () => <Mermaid source="flowchart LR; Catalogue --> Storybook; Storybook --> Docs; Storybook --> StaticBuild" /> };
export const MessageBubbleVariants: Story = { render: () => <div className="grid max-w-3xl gap-5"><MessageBubble variant="founder" seq={1} speaker="founder" speakerRole="founder" timestamp="2026-09-02T12:00:00Z" body="Please replace the catalogue." /><MessageBubble variant="worker" seq={2} speaker="frontend_engineer" speakerRole="worker" timestamp="2026-09-02T12:01:00Z" body="Implementation is in progress." /><MessageBubble variant="system" seq={3} timestamp="2026-09-02T12:02:00Z" body="Static build verified." /></div> };
export const PageHeaderWithActions: Story = { render: () => <PageHeader title="Design system" meta="37 stories · 7 exclusions" actions={<Button>Review</Button>} /> };

function RecipientsExample(): JSX.Element { const [value, setValue] = useState('front'); return <div className="max-w-xl"><RecipientsInput value={value} onChange={setValue} agents={agents} placeholder="Add recipients" /></div>; }
export const RecipientsInputInteraction: Story = { render: () => <RecipientsExample /> };
export const SparklineVariants: Story = { render: () => <div className="grid max-w-xl grid-cols-2 gap-5">{(['default', 'green', 'yellow', 'red'] as const).map((variant) => <Sparkline key={variant} data={[0.3, 0.7, 0.5, 0.9, 0.75]} variant={variant} />)}</div> };
export const StatValueFormats: Story = { render: () => <div className="flex gap-10"><StatValue value={3707054} format="tokens" suffix="cache" /><StatValue value={42} format="count" align="inline" /></div> };
export const StatusBadgeStates: Story = { render: () => <div className="flex flex-wrap gap-3">{(['open', 'archived', 'pending', 'in_progress', 'escalated', 'completed', 'failed', 'cancelled', 'superseded', 'blocked'] as const).map((status) => <StatusBadge key={status} status={status} />)}</div> };
export const ThreadHeaderStates: Story = { render: () => <div className="grid gap-6"><ThreadHeader threadId="THR-042" subject="Storybook replacement" status="open" participants={['founder', 'frontend_engineer']} dreamOriginated actions={<Button variant="outline">Archive</Button>} /><ThreadHeader threadId="THR-043" subject="Archived decision" status="archived" participants={['founder']} archiveSummary="Decision recorded." /></div> };
export const TypingBubbleStates: Story = { render: () => <div className="grid max-w-lg gap-5"><TypingBubble agentName="frontend_engineer" status="working" startedAt="2026-09-02T12:00:00Z" nowMs={Date.parse('2026-09-02T12:00:12Z')} /><TypingBubble agentName="code_reviewer" status="queued" startedAt={null} /></div> };
