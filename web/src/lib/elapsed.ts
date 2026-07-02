/**
 * formatElapsed — compact "Ns"/"Nm" elapsed label for in-flight indicators.
 *
 * Shared by the threads ResponderStatusStrip and the design-system TypingBubble
 * pattern (which both surface live elapsed while an agent replies). Lives in
 * `@/lib` so the design-system pattern can consume it without a cross-feature
 * import.
 */
export function formatElapsed(startedAt: string | null, nowMs: number): string {
  if (!startedAt) return '';
  const secs = Math.max(0, Math.floor((nowMs - Date.parse(startedAt)) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m`;
}
