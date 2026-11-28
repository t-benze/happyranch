import type { Meta, StoryObj } from '@storybook/react';
import { Avatar, type AvatarSize, type AvatarTone } from './Avatar';

const TONES: AvatarTone[] = ['founder', 'manager', 'worker', 'soft', 'neutral', 'guest'];
const SIZES: AvatarSize[] = ['sm', 'md', 'lg'];

const meta = {
  title: 'Design System/Coverage/Avatar',
  component: Avatar,
  tags: ['autodocs'],
  argTypes: {
    tone: { control: 'select', options: TONES },
    size: { control: 'select', options: SIZES },
  },
  args: { initials: 'EM', tone: 'manager', size: 'md' },
} satisfies Meta<typeof Avatar>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Coverage: Story = {};

/** Every tone at every size (22 / 30 / 34px). */
export const Variants: Story = {
  render: () => (
    <div className="grid max-w-xl gap-4" data-avatar-variants>
      {TONES.map((tone) => (
        <div key={tone} className="flex items-center gap-3">
          <span className="text-text-muted w-16 text-xs">{tone}</span>
          {SIZES.map((size) => (
            <Avatar key={size} initials={tone === 'founder' ? 'FO' : 'DA'} tone={tone} size={size} />
          ))}
        </div>
      ))}
    </div>
  ),
};
