import { cva } from 'class-variance-authority';

export const cardVariants = cva(
  'bg-surface-raised border-border-default shadow-pasture-sm border',
  {
    variants: {
      variant: {
        container: 'rounded-lg',
        entry: 'rounded-md',
      },
      padding: {
        default: 'p-5',
        none: '',
      },
    },
  },
);
