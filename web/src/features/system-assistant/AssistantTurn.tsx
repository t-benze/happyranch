/**
 * AssistantTurn — a single message turn in the assistant dock.
 *
 * Renders user messages, assistant markdown responses, ran: transparency
 * cards (detected client-side from the raw PTY output), and action chips
 * for ID click-through.
 */
import { Markdown } from '@/design-system/patterns/Markdown';

export interface AssistantMessage {
  role: 'user' | 'assistant';
  text: string;
  timestamp: string;
}

interface AssistantTurnProps {
  message: AssistantMessage;
  orgSlug?: string;
}

/** Pattern to detect ran: lines in the raw PTY output. */
const RAN_LINE_RE = /^ran:\s+(.+)$/im;

/** Pattern for IDs that can be deep-linked. */
const ID_RE = /\b((?:TASK|JOB|THR|PR|LRN|SR|KB)-\d+)\b/gi;

export function AssistantTurn({
  message,
  orgSlug,
}: AssistantTurnProps): JSX.Element {
  const isUser = message.role === 'user';

  // Detect ran: lines in assistant output for transparency cards.
  const ranMatches: string[] = [];
  let displayText = message.text;
  if (!isUser) {
    let match: RegExpExecArray | null;
    const ranRe = new RegExp(RAN_LINE_RE.source, 'gim');
    while ((match = ranRe.exec(message.text)) !== null) {
      ranMatches.push(match[1].trim());
    }
    // If ran: lines are present, strip them for the main display
    // and show them as separate cards.  Use a global regex so ALL
    // verbatim ran: lines are removed, not just the first one.
    if (ranMatches.length > 0) {
      displayText = message.text.replace(/^ran:\s+.+$/gim, '').trim();
    }
  }

  return (
    <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
      {/* ran: transparency cards — verbatim commands the assistant ran */}
      {ranMatches.map((cmd, i) => (
        <div
          key={`ran-${i}`}
          className="border-border bg-bg-subtle mb-2 w-full rounded border px-3 py-2 font-mono text-xs"
          aria-label={`ran: ${cmd}`}
        >
          <span className="text-fg-subtle">ran: </span>
          <span className="text-fg">{cmd}</span>
        </div>
      ))}

      {/* Message bubble */}
      {displayText && (
        <div
          className={[
            'max-w-[85%] rounded-lg px-3 py-2 text-sm',
            isUser
              ? 'bg-accent-muted border border-accent-ring text-fg'
              : 'bg-bg-raised border border-border-subtle text-fg',
          ].join(' ')}
        >
          {isUser ? (
            <p className="break-words whitespace-pre-wrap">{displayText}</p>
          ) : (
            <AssistantMarkdown text={displayText} orgSlug={orgSlug} />
          )}
        </div>
      )}

      {/* Timestamp */}
      <span className="text-fg-subtle mt-0.5 text-xs">
        {fmtTime(message.timestamp)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ID click-through in markdown
// ---------------------------------------------------------------------------

function AssistantMarkdown({
  text,
  orgSlug,
}: {
  text: string;
  orgSlug?: string;
}): JSX.Element {

  // Wrap IDs in clickable spans that navigate to the right surface.
  const processed = text.replace(ID_RE, (match) => {
    if (!orgSlug) return match;
    const href = idToHref(match, orgSlug);
    if (!href) return match;
    return `[${match}](${href})`;
  });

  if (processed !== text) {
    return <Markdown body={processed} />;
  }

  return <Markdown body={text} />;
}

function idToHref(id: string, slug: string): string | null {
  const prefix = id.split('-')[0]?.toUpperCase();
  switch (prefix) {
    case 'TASK':
      return `/orgs/${slug}/tasks/${id}`;
    case 'THR':
      return `/orgs/${slug}/threads/${id}`;
    case 'JOB':
      return `/orgs/${slug}/jobs/${id}`;
    case 'PR':
      return `/orgs/${slug}/artifacts`;
    case 'KB':
      return `/orgs/${slug}/kb/${id}`;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}
