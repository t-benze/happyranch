import type { Meta, StoryObj } from '@storybook/react';
import { ContentWrap } from './ContentWrap/ContentWrap';
import { DashboardLayout } from './DashboardLayout';
import { ThreadsLayout } from './ThreadsLayout';

const meta = { title: 'Design System/Layouts', tags: ['autodocs'], parameters: { layout: 'fullscreen' } } satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;
const panel = (label: string) => <div className="border-border-default bg-surface-raised rounded-lg border p-5">{label}</div>;

export const ContentWrapResponsive: Story = { render: () => <ContentWrap><div className="grid gap-4"><h1 className="text-h1">Bounded content</h1>{panel('ContentWrap follows the application content-width tokens.')}</div></ContentWrap> };
export const DashboardLayoutPopulated: Story = { render: () => <DashboardLayout health={panel('Runtime health')} pending={panel('Pending review')} activeByTeam={panel('Active by team')} blocked={panel('Blocked work')} /> };
export const ThreadsLayoutPopulated: Story = { render: () => <ThreadsLayout inbox={<div className="h-full p-4">Thread inbox</div>} detail={<div className="h-full p-6">Selected thread detail</div>} /> };
