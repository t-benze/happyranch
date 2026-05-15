/**
 * Active org-slug context.
 *
 * The slug always lives in the URL (``/orgs/:slug/...``). ``OrgProvider``
 * reads it from React Router and exposes it to consumers. There is no mutable
 * global state.
 */
import { createContext, useContext } from 'react';
import { useParams } from 'react-router-dom';

const OrgSlugContext = createContext<string | null>(null);

export function OrgProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  return (
    <OrgSlugContext.Provider value={slug ?? null}>{children}</OrgSlugContext.Provider>
  );
}

export function useOrgSlug(): string {
  const slug = useContext(OrgSlugContext);
  if (!slug) {
    throw new Error('useOrgSlug must be used inside <OrgProvider> with a :slug route param');
  }
  return slug;
}
