// Comments are not class lists: bg-not-a-token is ignored here.
/* Block comment: text-also-not-a-token is ignored too. */
export function App() {
  return (
    <main className="bg-surface text-ink border-line rounded-card shadow-soft text-body p-gutter max-w-content">
      <section className={`border-b border-t-0 rounded-tl rounded-xl shadow-sm max-w-md hover:bg-surface/50 text-center bg-transparent ring-offset-2 outline-offset-2 px-2 w-full gap-x-3`} />
      <aside className="bg-surface-page text-link-primary hover:border-danger-border md:p-huge rounded-blob shadow-glow" />
    </main>
  );
}
