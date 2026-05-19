/**
 * Pure transform from a closed talk's `transcript` markdown into a list of
 * `MessageBubble`s. The talk transcript has no enforced structure, so we
 * apply a best-effort speaker split on lines that start with
 *   `## founder` | `## agent` | `**founder:**` | `**agent_name:**`
 * and fall back to a single Markdown render of the entire body when no
 * markers are present.
 */
import { Markdown } from '@/design-system/patterns/Markdown';
import { MessageBubble } from '@/design-system/patterns/MessageBubble';

export interface TranscriptSection {
  speaker: 'founder' | 'agent' | null;
  body: string;
}

const SPEAKER_RE = /^(?:##\s+(founder|agent)\b|\*\*(founder|agent)[^*]*\*\*:?)/i;

export function splitTranscript(markdown: string): TranscriptSection[] {
  const lines = markdown.split('\n');
  const sections: TranscriptSection[] = [];
  let current: TranscriptSection | null = null;

  const flush = () => {
    if (current && current.body.trim()) sections.push(current);
    current = null;
  };

  for (const line of lines) {
    const m = line.match(SPEAKER_RE);
    if (m) {
      flush();
      const who = (m[1] ?? m[2] ?? '').toLowerCase();
      current = {
        speaker: who.startsWith('founder') ? 'founder' : 'agent',
        body: '',
      };
      continue;
    }
    if (!current) current = { speaker: null, body: '' };
    current.body += (current.body ? '\n' : '') + line;
  }
  flush();
  return sections;
}

interface Props {
  transcript: string;
  agentName: string;
  /** ISO timestamp shown in the bubble header — typically the talk's `started_at`. */
  timestamp: string;
}

export function TalkTranscript({ transcript, agentName, timestamp }: Props): JSX.Element {
  const sections = splitTranscript(transcript);
  const hasMarkers = sections.some((s) => s.speaker !== null);

  if (!hasMarkers) {
    return (
      <div className="flex h-full flex-col gap-2 overflow-auto px-4 py-3">
        <article className="border-border-subtle bg-surface-raised rounded-lg border p-4">
          <Markdown body={transcript} />
        </article>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 overflow-auto px-4 py-3">
      {sections.map((s, i) => (
        <MessageBubble
          key={i}
          variant={s.speaker === 'founder' ? 'founder' : 'worker'}
          seq={i}
          speaker={s.speaker === 'founder' ? 'founder' : agentName}
          speakerRole={s.speaker === 'founder' ? 'founder' : 'worker'}
          timestamp={timestamp}
          body={s.body}
        />
      ))}
    </div>
  );
}
