import { useCallback, useState } from 'react';

export type Density = 'comfortable' | 'compact';
const KEY = 'happyranch.density';

function readInitial(): Density {
  const v = typeof window !== 'undefined' ? window.localStorage.getItem(KEY) : null;
  return v === 'compact' ? 'compact' : 'comfortable';
}

export function useDensity(): {
  density: Density;
  setDensity: (d: Density) => void;
} {
  const [density, setDensityState] = useState<Density>(readInitial);
  const setDensity = useCallback((d: Density) => {
    setDensityState(d);
    if (typeof window !== 'undefined') window.localStorage.setItem(KEY, d);
  }, []);
  return { density, setDensity };
}
