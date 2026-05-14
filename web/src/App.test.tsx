import { render, screen } from '@testing-library/react';
import { App } from './App';

test('renders OPC heading', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: /OPC/i })).toBeInTheDocument();
});
