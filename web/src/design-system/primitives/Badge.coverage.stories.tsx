import type { Meta, StoryObj } from '@storybook/react';
import { TONE_CLASS } from '../patterns/semanticTone';
import { Badge } from './Badge';

function AllTones(): JSX.Element {
  return (
    <div className="flex flex-wrap gap-2">
      <Badge toneClass={TONE_CLASS.positive}>Active</Badge>
      <Badge toneClass={TONE_CLASS.neutral}>Archived</Badge>
      <Badge toneClass={TONE_CLASS.info}>Not yet used</Badge>
      <Badge toneClass={TONE_CLASS.attention}>Same display name as effort</Badge>
      <Badge toneClass={TONE_CLASS.danger}>Refused</Badge>
    </div>
  );
}

const meta = { title: 'Design System/Coverage/Badge', component: AllTones, tags: ['autodocs'] } satisfies Meta<typeof AllTones>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Coverage: Story = {};
