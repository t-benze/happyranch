import type { Meta, StoryObj } from '@storybook/react';
import { CardHeader } from './Card';

const meta = {
  title: 'Design System/Coverage/CardHeader',
  component: CardHeader,
  tags: ['autodocs'],
  args: { title: 'Knowledge candidates', meta: '4 pending' },
} satisfies Meta<typeof CardHeader>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};
