/**
 * SHA-256 hex of a UTF-8 SKILL.md — the daemon's custom-skill version
 * `content_hash` (THR-387 S6-W2a Check result). Null when Web Crypto is
 * unavailable: the caller must then keep a result unknown.
 */
export async function skillMdHash(skillMd: string): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(skillMd));
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  } catch {
    return null;
  }
}
