/**
 * Drawer primitive — Radix Dialog-backed slide-in from the right.
 * 480px wide, non-modal-feel (page stays visible behind thin scrim).
 *
 * Usage:
 *   <Drawer open={open} onOpenChange={onClose}>
 *     <DrawerContent>
 *       <DrawerTitle>Detail</DrawerTitle>
 *       ...body...
 *     </DrawerContent>
 *   </Drawer>
 */
import * as DialogPrimitive from '@radix-ui/react-dialog';
import * as React from 'react';

import { cn } from '@/lib/utils';

export const Drawer = DialogPrimitive.Root;
export const DrawerTrigger = DialogPrimitive.Trigger;
export const DrawerClose = DialogPrimitive.Close;
export const DrawerTitle = DialogPrimitive.Title;
export const DrawerDescription = DialogPrimitive.Description;

export const DrawerContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-bg/40" />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        'fixed right-0 top-0 z-50 flex h-full w-[480px] flex-col',
        'bg-surface-raised border-l border-border-subtle shadow-xl',
        'data-[state=open]:animate-in data-[state=closed]:animate-out',
        'data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right',
        className,
      )}
      {...props}
    >
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DrawerContent.displayName = 'DrawerContent';

export const meta = {
  name: 'Drawer',
  layer: 'primitive',
  import: '@/design-system/primitives/Drawer',
  variants: {},
  consumes: ['components.drawer'],
  example:
    "<Drawer open={false} onOpenChange={() => {}}><DrawerContent><DrawerTitle>Detail</DrawerTitle></DrawerContent></Drawer>",
} as const;
