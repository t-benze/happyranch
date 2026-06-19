/**
 * LinkifyId — shared pattern that turns THR-/TASK-/JOB-/PR# tokens into
 * click-through links.
 *
 * THR-* → /orgs/:slug/threads/:id
 * TASK-* → /orgs/:slug/tasks/:id
 * JOB-* → /orgs/:slug/jobs/:id
 * PR#N → stored PR URL IF available via `prUrls` map, else plain text
 *   (do NOT fabricate a GitHub link — honesty lens P1).
 *
 * Usage: <LinkifyId text="task TASK-042 dispatched" slug="myorg" />
 *
 * Pure prop-driven; returns ReactNode fragments interspersing text and Links.
 */
import { Link } from 'react-router-dom';

/** Map from PR#N to its stored PR URL. Omit entries for PR numbers that have
 *  no stored URL — they render as plain text (P1). */
export interface LinkifyPrUrls {
  [token: string]: string;
}

interface LinkifyIdProps {
  /** Raw text that may contain THR-/TASK-/JOB-/PR# tokens. */
  text: string;
  /** Active org slug for constructing detail-route links. */
  slug: string;
  /** Optional map of PR#N → stored PR URL. If absent, all PR# tokens
   *  render as plain text. */
  prUrls?: LinkifyPrUrls;
}

/** Regex matching THR-NNN, TASK-NNN, JOB-NNN, or PR#NNN tokens.
 *  Matches as word boundaries to avoid mid-word false-positives. */
const ID_PATTERN = /\b((?:THR|TASK|JOB)-[A-Za-z0-9]+|PR#\d+)\b/g;

const PREFIX_TO_PATH: Record<string, string> = {
  'THR-': 'threads',
  'TASK-': 'tasks',
  'JOB-': 'jobs',
};

function tokenPrefix(token: string): string | null {
  for (const prefix of Object.keys(PREFIX_TO_PATH)) {
    if (token.startsWith(prefix)) return prefix;
  }
  return null;
}

export function LinkifyId({ text, slug, prUrls }: LinkifyIdProps): JSX.Element {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  const regex = new RegExp(ID_PATTERN.source, 'g');
  while ((match = regex.exec(text)) !== null) {
    // Text before this match
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    lastIndex = regex.lastIndex;

    const token = match[1];
    const prefix = tokenPrefix(token);

    if (prefix) {
      // THR-/TASK-/JOB- → internal detail route
      parts.push(
        <Link
          key={`${token}-${match.index}`}
          to={`/orgs/${slug}/${PREFIX_TO_PATH[prefix]}/${token}`}
          className="text-accent font-mono text-xs hover:underline"
        >
          {token}
        </Link>,
      );
    } else if (token.startsWith('PR#')) {
      // PR#N — only link if we have a stored URL
      const prUrl = prUrls?.[token];
      if (prUrl) {
        parts.push(
          <a
            key={`${token}-${match.index}`}
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent font-mono text-xs hover:underline"
          >
            {token}
          </a>,
        );
      } else {
        parts.push(
          <span key={`${token}-${match.index}`} className="font-mono text-xs">
            {token}
          </span>,
        );
      }
    }
  }

  // Trailing text after last match
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  // If no tokens found, return the raw text
  if (parts.length === 0) {
    return <>{text}</>;
  }

  return <>{parts}</>;
}

export const meta = {
  name: "LinkifyId",
  layer: "pattern",
  import: "@/design-system/patterns/LinkifyId",
  variants: {},
  consumes: ["components.link"],
  example: "<LinkifyId text='task TASK-042 dispatched' slug='myorg' />",
} as const;
