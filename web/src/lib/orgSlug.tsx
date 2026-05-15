/**
 * Active org-slug context.
 *
 * The slug always lives in the URL (``/orgs/:slug/...``). ``OrgProvider``
 * reads it from React Router and exposes it to consumers. There is no mutable
 * global state.
 *
 * Under the prototype harness, ``PrototypeProvider`` writes a hardcoded
 * mock slug to the same context so compositions render unchanged.
 */
import { createContext, useContext, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';

const OrgSlugContext = createContext<string | null>(null);

export function OrgProvider({ children }: { children: ReactNode }): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return (
    <OrgSlugContext.Provider value={slug ?? null}>{children}</OrgSlugContext.Provider>
  );
}

/** Test/mock-only: inject a fixed slug for compositions that build nav paths. */
export function StaticOrgProvider({
  slug,
  children,
}: {
  slug: string;
  children: ReactNode;
}): JSX.Element {
  return <OrgSlugContext.Provider value={slug}>{children}</OrgSlugContext.Provider>;
}

export function useOrgSlug(): string {
  const slug = useContext(OrgSlugContext);
  if (!slug) {
    throw new Error('useOrgSlug must be used inside <OrgProvider> with a :slug route param');
  }
  return slug;
}

/** Safe variant: returns null when no slug context is mounted. */
export function useOrgSlugOptional(): string | null {
  return useContext(OrgSlugContext);
}
