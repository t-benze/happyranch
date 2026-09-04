import type { Preview } from '@storybook/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { TooltipProvider } from '@/design-system/primitives/Tooltip';
import '../src/styles.css';

const queryClient = new QueryClient({
  defaultOptions: { queries: { enabled: false, retry: false } },
});

const preview: Preview = {
  decorators: [
    (Story) => (
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>
            <div className="bg-surface-canvas text-text-primary min-h-screen px-4 py-6 sm:px-8 lg:px-12">
              <Story />
            </div>
          </TooltipProvider>
        </QueryClientProvider>
      </MemoryRouter>
    ),
  ],
  globalTypes: {
    theme: {
      description: 'Semantic token theme',
      toolbar: { icon: 'paintbrush', items: ['dark', 'light'] },
    },
  },
  initialGlobals: { theme: 'light' },
  parameters: {
    controls: { expanded: true },
    options: { storySort: { order: ['Pasture', ['Overview', 'Foundations', 'Components', 'Patterns', 'Page States', 'Layouts'], 'Design System'] } },
  },
};

preview.decorators?.push((Story, context) => {
  document.documentElement.dataset.theme = context.globals.theme === 'light' ? 'light' : 'dark';
  return <Story />;
});

export default preview;
