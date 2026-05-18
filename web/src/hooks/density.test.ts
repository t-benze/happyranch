import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDensity } from './density';

describe('useDensity', () => {
  beforeEach(() => localStorage.clear());

  it('defaults to comfortable', () => {
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('comfortable');
  });

  it('reads persisted value', () => {
    localStorage.setItem('grassland.density', 'compact');
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('compact');
  });

  it('persists on toggle', () => {
    const { result } = renderHook(() => useDensity());
    act(() => result.current.setDensity('compact'));
    expect(result.current.density).toBe('compact');
    expect(localStorage.getItem('grassland.density')).toBe('compact');
  });

  it('ignores invalid persisted value', () => {
    localStorage.setItem('grassland.density', 'garbage');
    const { result } = renderHook(() => useDensity());
    expect(result.current.density).toBe('comfortable');
  });
});
