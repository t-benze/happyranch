/**
 * Tabs primitive — shadcn/ui canonical (Radix Tabs), styled against our
 * semantic tokens.
 *
 * Two visual variants:
 *   - `pills`     — default; rounded chip-style triggers for status filters
 *                   (inbox: open / archived / abandoned).
 *   - `underline` — TODO: reserved for future Audit sub-tabs. Not implemented
 *                   in PR 5; document here so the contract is explicit.
 *
 * Usage:
 *   <Tabs value={status} onValueChange={(v) => setStatus(v as StatusTab)}>
 *     <TabsList>
 *       <TabsTrigger value="open">open</TabsTrigger>
 *       <TabsTrigger value="archived">archived</TabsTrigger>
 *     </TabsList>
 *   </Tabs>
 */
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/lib/utils';

const Tabs = TabsPrimitive.Root;

const tabsListVariants = cva('inline-flex items-center', {
  variants: {
    variant: {
      pills: 'gap-1',
      // underline TODO — see file header.
      underline: 'border-border-default gap-3 border-b',
    },
  },
  defaultVariants: { variant: 'pills' },
});

interface TabsListProps
  extends React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>,
    VariantProps<typeof tabsListVariants> {}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  TabsListProps
>(({ className, variant, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    data-variant={variant ?? 'pills'}
    className={cn(tabsListVariants({ variant }), className)}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

const tabsTriggerVariants = cva(
  'text-caption focus-visible:ring-ring inline-flex items-center justify-center whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        pills:
          'text-text-muted hover:text-text-primary data-[state=active]:bg-surface-raised data-[state=active]:text-text-primary rounded-md px-2 py-0.5',
        underline:
          'text-text-muted hover:text-text-primary data-[state=active]:border-accent-default data-[state=active]:text-text-primary -mb-px border-b-2 border-transparent px-1 pb-1',
      },
    },
    defaultVariants: { variant: 'pills' },
  },
);

interface TabsTriggerProps
  extends React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>,
    VariantProps<typeof tabsTriggerVariants> {}

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  TabsTriggerProps
>(({ className, variant, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(tabsTriggerVariants({ variant }), className)}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
      className,
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };

export const meta = {
  name: "Tabs",
  layer: "primitive",
  import: "@/design-system/primitives/Tabs",
  variants: {
    variant: ["pills", "underline"],
  },
  consumes: ["components.tabs"],
  example: "<Tabs value='open'><TabsList><TabsTrigger value='open'>open</TabsTrigger></TabsList></Tabs>",
} as const;
