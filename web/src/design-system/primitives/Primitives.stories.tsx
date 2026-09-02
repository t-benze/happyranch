import type { Meta, StoryObj } from '@storybook/react';
import { Button } from './Button';
import { Input } from './Input';
import { Label } from './Label';
import { Textarea } from './Textarea';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './Dialog';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle, DrawerTrigger } from './Drawer';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from './DropdownMenu';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './Select';
import { SubTabBar } from './SubTabBar';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './Tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from './Tooltip';

const meta = {
  title: 'Design System/Primitives',
  component: Button,
  tags: ['autodocs'],
  parameters: { docs: { description: { component: 'Token-backed interaction primitives. Use the Controls panel to inspect Button variants and sizes.' } } },
  argTypes: {
    variant: { control: 'select', options: ['default', 'destructive', 'outline', 'secondary', 'ghost', 'link'] },
    size: { control: 'select', options: ['default', 'sm', 'lg', 'icon'] },
  },
  args: { children: 'Send', variant: 'default', size: 'default' },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ButtonStates: Story = { render: (args) => <div className="flex flex-wrap gap-3"><Button {...args} /><Button {...args} disabled>Disabled</Button></div> };
export const InputStates: Story = { render: () => <div className="grid max-w-sm gap-3"><Label htmlFor="story-input">Subject</Label><Input id="story-input" placeholder="Refund policy" /><Input value="Read only" readOnly /><Input placeholder="Disabled" disabled /></div> };
export const LabelState: Story = { render: () => <Label htmlFor="label-example">Accessible field label</Label> };
export const TextareaStates: Story = { render: () => <div className="grid max-w-lg gap-3"><Textarea placeholder="Compose…" /><Textarea value="Disabled content" disabled readOnly /></div> };
export const DialogInteraction: Story = { render: () => <Dialog><DialogTrigger asChild><Button>Open dialog</Button></DialogTrigger><DialogContent><DialogHeader><DialogTitle>Confirm action</DialogTitle><DialogDescription>This portal-backed example is safe and fully local.</DialogDescription></DialogHeader></DialogContent></Dialog> };
export const DrawerInteraction: Story = { render: () => <Drawer><DrawerTrigger asChild><Button>Open drawer</Button></DrawerTrigger><DrawerContent><DrawerTitle>Task detail</DrawerTitle><DrawerDescription>Isolated drawer content.</DrawerDescription></DrawerContent></Drawer> };
export const DropdownMenuInteraction: Story = { render: () => <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline">Open menu</Button></DropdownMenuTrigger><DropdownMenuContent><DropdownMenuItem>Rename</DropdownMenuItem><DropdownMenuItem>Archive</DropdownMenuItem></DropdownMenuContent></DropdownMenu> };
export const SelectInteraction: Story = { render: () => <Select defaultValue="open"><SelectTrigger className="w-48"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="open">Open</SelectItem><SelectItem value="archived">Archived</SelectItem></SelectContent></Select> };
export const SubTabBarStates: Story = { render: () => <SubTabBar active="activity" tabs={[{ value: 'activity', label: 'Activity', to: '/activity' }, { value: 'details', label: 'Details', to: '/details' }]} /> };
export const TabsVariants: Story = { render: () => <div className="grid gap-6">{(['pills', 'underline', 'segmented'] as const).map((variant) => <Tabs key={variant} defaultValue="open"><TabsList variant={variant}><TabsTrigger value="open">Open</TabsTrigger><TabsTrigger value="closed">Closed</TabsTrigger></TabsList><TabsContent value="open">{variant} tabs</TabsContent></Tabs>)}</div> };
export const TooltipInteraction: Story = { render: () => <Tooltip defaultOpen><TooltipTrigger asChild><Button variant="ghost">Hover or focus</Button></TooltipTrigger><TooltipContent>Keyboard shortcut: ?</TooltipContent></Tooltip> };
