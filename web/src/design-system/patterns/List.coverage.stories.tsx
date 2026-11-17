import type { Meta, StoryObj } from '@storybook/react';
import { List, Row, RowAction } from './List';

const meta = {
  title: 'Design System/Coverage/List',
  component: List,
  tags: ['autodocs'],
  argTypes: { variant: { control: 'select', options: ['container', 'entry'] } },
  args: {
    'aria-label': 'Jobs',
    children: [
      <Row key="static">Static row: 16px / 20px padding, hairline divider.</Row>,
      <Row key="link" padding="none"><RowAction asChild><a href="#job-2" className="block">Link row: the whole row is the link and hovers to surface-2.</a></RowAction></Row>,
      <Row key="button" padding="none"><RowAction>Button row (RowAction without asChild)</RowAction></Row>,
    ],
  },
} satisfies Meta<typeof List>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};

export const Variants: Story = {
  parameters: { docs: { description: { story: 'One card per group with rows inside it, instead of a stack of cards. container (18px) is the default; entry (12px) is for a small nested list. A selected row keeps its own fill.' } } },
  render: () => (
    <div className="grid max-w-3xl gap-8" data-list-variants>
      <section aria-label="Running" className="flex flex-col gap-2">
        <p className="text-eyebrow text-text-muted uppercase">Running · 2</p>
        <List>
          <Row padding="none"><RowAction asChild><a href="#job-1" className="flex items-center gap-4"><span className="text-mono-sm text-accent-text">JOB-0101</span><span className="text-sm">npm run build</span></a></RowAction></Row>
          <Row padding="none"><RowAction asChild><a href="#job-2" className="flex items-center gap-4"><span className="text-mono-sm text-accent-text">JOB-0102</span><span className="text-sm">npm run lint</span></a></RowAction></Row>
        </List>
      </section>
      <List variant="entry" aria-label="Roster">
        <Row padding="none"><RowAction className="bg-accent-muted">Selected row</RowAction></Row>
        <Row padding="none"><RowAction>Unselected row</RowAction></Row>
      </List>
    </div>
  ),
};
