import type { Meta, StoryObj } from '@storybook/react';
import { Button } from './Button';
import { Card, CardHeader } from './Card';

const meta = {
  title: 'Design System/Coverage/Card',
  component: Card,
  tags: ['autodocs'],
  argTypes: {
    variant: { control: 'select', options: ['container', 'entry'] },
    padding: { control: 'select', options: ['default', 'none'] },
  },
  args: { variant: 'container', padding: 'default', children: 'A Pasture card: surface, 1px border, warm shadow, 20px padding.' },
} satisfies Meta<typeof Card>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};

export const Variants: Story = {
  parameters: { docs: { description: { story: 'container (18px) holds a page section or a list; entry (12px) is one item in a feed or grid of separate cards. padding="none" lets a CardHeader or List own the padding.' } } },
  render: () => (
    <div className="grid max-w-3xl gap-6" data-card-variants>
      <Card>Container card, padding 20.</Card>
      <Card variant="entry">Entry card, padding 20.</Card>
      <Card padding="none">
        <CardHeader title="Recent runs" meta="3 today" actions={<Button size="sm" variant="outline">View all</Button>} />
        <p className="text-body text-text-secondary p-5">Card body below a CardHeader.</p>
      </Card>
    </div>
  ),
};
