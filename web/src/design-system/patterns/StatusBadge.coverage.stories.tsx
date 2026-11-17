import type { Meta, StoryObj } from '@storybook/react';
import { StatusBadge } from './StatusBadge';

const meta = { title: 'Design System/Coverage/StatusBadge', component: StatusBadge, tags: ['autodocs'] } satisfies Meta<typeof StatusBadge>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};
export const InProgress: Story = { args: { status: 'in_progress' } };
export const Escalated: Story = { args: { status: 'escalated' } };
