import { BrowserRouter } from 'react-router-dom';
import { AppProvider, makeQueryClient } from '@/design-system/providers/AppProvider';
import { AppRoutes } from './routes';

export { makeQueryClient };

export function App(): JSX.Element {
  return (
    <BrowserRouter>
      <AppProvider>
        <AppRoutes />
      </AppProvider>
    </BrowserRouter>
  );
}
