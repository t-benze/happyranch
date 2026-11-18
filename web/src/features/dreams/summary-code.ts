/**
 * Dream summary code contrast (THR-376 r2 Dr1).
 *
 * The shared Markdown pattern highlights fenced code with the app-wide
 * GitHub Dark Dimmed theme, whose literal syntax colours sit on the
 * light-mode raised surface of `.gl-prose pre` at <4.5:1. Dream summaries
 * repaint the code element and every syntax descendant with the semantic
 * secondary text token (>=4.5:1 on the raised surface in both themes) and
 * repaint per-token highlight backgrounds (diff add/del) with the same
 * raised-surface token the enclosing pre uses. Scoped to Dreams compositions;
 * the shared Markdown pattern and global highlight theme are unchanged.
 */
export const SUMMARY_CODE_TOKENS =
  '[&_.hljs]:text-text-secondary! [&_.hljs_*]:text-text-secondary! [&_.hljs_*]:bg-surface-raised!';
