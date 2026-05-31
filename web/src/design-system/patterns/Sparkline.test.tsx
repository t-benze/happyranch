import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Sparkline } from './Sparkline';

describe('Sparkline', () => {
  it('renders an svg with the requested width and height', () => {
    const { container } = render(
      <Sparkline data={[0.5, 0.6, 0.7]} width={100} height={20} />,
    );
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('width')).toBe('100');
    expect(svg?.getAttribute('height')).toBe('20');
  });

  it('renders a polyline when data is non-empty', () => {
    const { container } = render(<Sparkline data={[0.5, 0.6]} />);
    expect(container.querySelector('polyline')).not.toBeNull();
  });

  it('accepts an empty array without crashing', () => {
    const { container } = render(<Sparkline data={[]} />);
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.querySelector('polyline')).toBeNull();
  });

  it('applies tier variant as a stroke class', () => {
    const { container } = render(<Sparkline data={[0.5, 0.6]} variant="green" />);
    const polyline = container.querySelector('polyline');
    expect(polyline?.getAttribute('class') ?? '').toMatch(/tier-green/);
  });
});
