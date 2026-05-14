import { forwardRef } from 'react';
import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent hover:bg-accent-hover text-white',
  secondary: 'bg-bg-raised hover:bg-border text-fg border border-border',
  ghost: 'bg-transparent hover:bg-bg-raised text-fg',
  danger: 'bg-tier-red hover:bg-red-600 text-white',
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = 'primary', className = '', ...rest }, ref) => (
    <button
      ref={ref}
      className={`inline-flex items-center justify-center rounded px-3 py-1.5 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
      {...rest}
    />
  ),
);
Button.displayName = 'Button';
