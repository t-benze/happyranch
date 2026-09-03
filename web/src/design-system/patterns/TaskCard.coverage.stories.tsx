import type { Meta, StoryObj } from '@storybook/react';
import { TaskCard } from './TaskCard';

const meta = { title: 'Design System/Coverage/TaskCard', component: TaskCard, tags: ['autodocs'] } satisfies Meta<typeof TaskCard>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};
