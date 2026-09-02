import type { Meta, StoryObj } from '@storybook/react';

const meta = { title: 'Design System/Foundations/Semantic Tokens', tags: ['autodocs'] } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

const swatches = [
  ['Surface', 'bg-surface'], ['Raised', 'bg-surface-raised'], ['Sunken', 'bg-surface-sunken'],
  ['Accent', 'bg-accent-default'], ['Success', 'bg-feedback-success'], ['Warning', 'bg-feedback-warning'], ['Danger', 'bg-feedback-danger'],
];

export const ThemeAndTokens: Story = {
  render: () => <div><h1 className="text-h1">Semantic tokens</h1><p className="text-body text-text-secondary mt-2">Switch light/dark from the toolbar. Components consume named tokens; no daemon connection is made.</p><div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-4">{swatches.map(([name, cls]) => <div key={name} className={`border-border-default rounded-lg border p-4 ${cls}`}><span className="text-caption font-semibold">{name}</span></div>)}</div><div className="mt-6 space-y-2"><p className="text-h2">Heading scale</p><p className="text-body">Body text</p><p className="text-caption text-text-muted">Caption and muted text</p><code className="text-mono-sm">TASK-6530</code></div></div>,
};
