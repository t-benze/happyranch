import type { Meta, StoryObj } from '@storybook/react';
import { useState } from 'react';
import { SegmentedControl, type SegmentedControlSize } from './SegmentedControl';
import { Tabs, TabsList, TabsTrigger } from './Tabs';

const WINDOWS = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: 'all', label: 'All' },
] as const;
type Win = (typeof WINDOWS)[number]['value'];

function Controlled({ size = 'default', initial = '7d' }: { size?: SegmentedControlSize; initial?: Win }): JSX.Element {
  const [value, setValue] = useState<Win>(initial);
  return <SegmentedControl aria-label="Time window" size={size} value={value} onValueChange={setValue} options={WINDOWS} />;
}

const meta = {
  title: 'Design System/Coverage/SegmentedControl',
  component: Controlled,
  tags: ['autodocs'],
  argTypes: { size: { control: 'select', options: ['default', 'sm'] } },
  args: { size: 'default' },
} satisfies Meta<typeof Controlled>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Coverage: Story = {};

function GroupBy(): JSX.Element {
  const [value, setValue] = useState<'status' | 'agent' | 'thread'>('status');
  return (
    <SegmentedControl
      aria-label="Group by"
      value={value}
      onValueChange={setValue}
      options={[
        { value: 'status', label: 'Status' },
        { value: 'agent', label: 'Agent' },
        { value: 'thread', label: 'Thread' },
      ]}
    />
  );
}

function WithDisabled(): JSX.Element {
  const [value, setValue] = useState<'current' | 'removed' | 'archived'>('current');
  return (
    <SegmentedControl
      aria-label="View"
      value={value}
      onValueChange={setValue}
      options={[
        { value: 'current', label: 'Current' },
        { value: 'removed', label: 'Removed' },
        { value: 'archived', label: 'Archived', disabled: true },
      ]}
    />
  );
}

export const Variants: Story = {
  parameters: {
    docs: {
      description: {
        story:
          'Single-select radio group: default and sm sizes, a three-option group-by, a disabled option, and Tabs variant="segmented" (same classes, for real tabs with panels).',
      },
    },
  },
  render: () => (
    <div className="grid max-w-xl gap-6" data-segmented-variants>
      <Controlled />
      <Controlled size="sm" initial="24h" />
      <GroupBy />
      <WithDisabled />
      <Tabs defaultValue="open">
        <TabsList variant="segmented" aria-label="Tabs segmented">
          <TabsTrigger variant="segmented" value="open">Open</TabsTrigger>
          <TabsTrigger variant="segmented" value="closed">Closed</TabsTrigger>
        </TabsList>
      </Tabs>
    </div>
  ),
};
