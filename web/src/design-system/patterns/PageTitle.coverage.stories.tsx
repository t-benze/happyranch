import type { Meta, StoryObj } from '@storybook/react';
import { PageTitle } from './PageTitle';

const meta = { title: 'Design System/Coverage/PageTitle', component: PageTitle, tags: ['autodocs'], args: { title: 'Tasks' } } satisfies Meta<typeof PageTitle>;
export default meta;
type Story = StoryObj;
export const Coverage: Story = {};
