import type { Meta, StoryObj } from '@storybook/react';
import { Markdown } from './Markdown';

const meta = { title: 'Design System/Coverage/Markdown', component: Markdown, tags: ['autodocs'] } satisfies Meta<typeof Markdown>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};
