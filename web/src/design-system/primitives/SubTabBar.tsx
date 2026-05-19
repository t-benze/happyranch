/**
 * SubTabBar — Radix Tabs styled as a navigation strip that swaps URLs.
 * Used to mount three-way sub-routes inside a feature page (e.g. Audit).
 *
 * Renders one trigger per tab; clicking a trigger navigates to `tab.to`.
 * The active tab is supplied as a prop (parents derive it from the URL).
 */
import * as Tabs from '@radix-ui/react-tabs';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

export interface SubTab {
  value: string;
  label: string;
  to: string;
}

export interface SubTabBarProps {
  tabs: SubTab[];
  active: string;
  className?: string;
}

export function SubTabBar({ tabs, active, className }: SubTabBarProps): JSX.Element {
  const navigate = useNavigate();
  return (
    <Tabs.Root
      value={active}
      onValueChange={(v) => {
        const target = tabs.find((t) => t.value === v);
        if (target) navigate(target.to);
      }}
      className={cn('border-border-subtle border-b', className)}
    >
      <Tabs.List className="flex gap-1 px-3">
        {tabs.map((t) => (
          <Tabs.Trigger
            key={t.value}
            value={t.value}
            className={cn(
              '-mb-px border-b-2 border-transparent px-3 py-2 text-sm',
              'data-[state=active]:border-accent data-[state=active]:text-fg',
              'data-[state=inactive]:text-fg-muted hover:text-fg',
            )}
          >
            {t.label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
    </Tabs.Root>
  );
}

export const meta = {
  name: 'SubTabBar',
  layer: 'primitive',
  import: '@/design-system/primitives/SubTabBar',
  variants: {},
  consumes: ['components.subtabbar'],
  example: '<SubTabBar tabs={[]} active="activity" />',
} as const;
