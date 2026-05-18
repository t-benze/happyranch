/**
 * Label primitive — shadcn/ui canonical (Radix Label), styled against
 * the `typography.scale.label` token. Pairs with Input/Textarea/Select for
 * accessible form fields.
 */
import * as LabelPrimitive from '@radix-ui/react-label';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';

import { cn } from '@/lib/utils';

const labelVariants = cva(
  'text-label leading-none font-medium peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
);

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root> &
    VariantProps<typeof labelVariants>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root
    ref={ref}
    className={cn(labelVariants(), className)}
    {...props}
  />
));
Label.displayName = LabelPrimitive.Root.displayName;

export const meta = {
  name: "Label",
  layer: "primitive",
  import: "@/design-system/primitives/Label",
  variants: {},
  consumes: ["components.label"],
  example: "<Label htmlFor='subject'>Subject</Label>",
} as const;
