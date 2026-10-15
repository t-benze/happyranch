/** NEW-message lexical contract, mirrored by thread_mentions.py.
 * Offsets refer to the raw body (UTF16 for caret); no rewriting or Markdown AST.
 */
const tokenPattern = /[A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*/y;
const boundaries = new Set(Array.from('([{\"\''));
function mentionSpace(char: string): boolean {
  const cp = char.charCodeAt(0);
  return (cp >= 9 && cp <= 13) || [32,133,160,5760,8232,8233,8239,8287,12288].includes(cp)
    || (cp >= 8192 && cp <= 8202);
}
function backslashStart(text: string, offset: number): number {
  let start = offset;
  while (start > 0 && text[start - 1] === '\\') start--;
  return start;
}
function codeMask(text: string): Uint8Array {
  const mask = new Uint8Array(text.length);
  let regionStart: number | null = null;
  let regionEnd = 0;
  let fence: { char: string; length: number } | null = null;
  const finishRegion = () => {
    if (regionStart === null) return;
    const runs = Array.from(text.slice(regionStart, regionEnd).matchAll(/`+/g), m => ({
      start: regionStart! + m.index!, end: regionStart! + m.index! + m[0].length,
    }));
    const nextRun = new Map<number, number>();
    const closers = new Map<number, number>();
    for (let i = runs.length - 1; i >= 0; i--) {
      const length = runs[i].end - runs[i].start;
      const closer = nextRun.get(length);
      if (closer !== undefined) closers.set(i, closer);
      nextRun.set(length, i);
    }
    let i = 0;
    while (i < runs.length) {
      const start = runs[i].start;
      const closer = closers.get(i);
      if ((start - backslashStart(text, start)) % 2 === 0 && closer !== undefined) {
        mask.fill(1, start, runs[closer].end);
        i = closer + 1;
      } else i++;
    }
    regionStart = null;
  };
  for (const line of text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g)) {
    const start = line.index!;
    const end = start + line[0].length;
    if (start === end) continue;
    const content = line[0].replace(/[\r\n]+$/, '');
    const marker = content.match(/^ {0,3}(`{3,}|~{3,})([^\r\n]*)$/);
    if (fence) {
      mask.fill(1, start, end);
      if (marker && marker[1][0] === fence.char
          && marker[1].length >= fence.length && /^[ \t]*$/.test(marker[2])) fence = null;
      continue;
    }
    if (/^ {0,3}>/.test(content)) {
      finishRegion(); mask.fill(1, start, end); continue;
    }
    if (marker && (marker[1][0] !== '`' || !marker[2].includes('`'))) {
      finishRegion();
      fence = { char: marker[1][0], length: marker[1].length };
      mask.fill(1, start, end);
      continue;
    }
    if (/^[ \t]*$/.test(content)) { finishRegion(); continue; }
    if (regionStart === null) regionStart = start;
    regionEnd = end;
  }
  finishRegion();
  return mask;
}
export interface Mention { tokenStart: number; end: number; query: string }
export function scanMentions(text: string): Mention[] {
  const mask = codeMask(text);
  const result: Mention[] = [];
  for (let offset = 0; offset < text.length; offset++) {
    if (text[offset] !== '@' || mask[offset]) continue;
    const boundary = backslashStart(text, offset);
    if ((offset - boundary) % 2 !== 0 || (boundary > 0
        && !mentionSpace(text[boundary - 1]) && !boundaries.has(text[boundary - 1]))) continue;
    tokenPattern.lastIndex = offset + 1;
    const query = tokenPattern.exec(text)?.[0] ?? '';
    if (!query && offset + 1 < text.length && text.charCodeAt(offset + 1) > 127) continue;
    result.push({ tokenStart: offset, end: offset + 1 + query.length, query });
  }
  return result;
}
export function proseAddresses(text: string): string[] {
  return [...new Set(scanMentions(text).map(m => m.query).filter(Boolean))];
}
/** Use the complete body so closed code and paragraph/container exits agree. */
export function openMention(text: string, caret: number): { query: string; tokenStart: number } | null {
  for (const mention of scanMentions(text)) {
    if (caret > mention.tokenStart && caret <= mention.end) {
      // An empty hint is valid only at @, not before an invalid token character.
      return { query: text.slice(mention.tokenStart + 1, caret), tokenStart: mention.tokenStart };
    }
  }
  return null;
}
