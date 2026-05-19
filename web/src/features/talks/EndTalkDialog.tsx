import { useEffect, useId, useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Textarea } from '@/design-system/primitives/Textarea';
import { FormField } from '@/design-system/patterns/FormField';
import { ApiError } from '@/lib/api';
import { useEndTalk } from '@/hooks/talks';
import { describeTalksError } from './strings';

interface Props {
  talkId: string;
  open: boolean;
  onClose: () => void;
}

function splitCsv(s: string): string[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

export function EndTalkDialog({ talkId, open, onClose }: Props): JSX.Element {
  const end = useEndTalk(talkId);
  const idBase = useId();
  const [summary, setSummary] = useState('');
  const [transcript, setTranscript] = useState('');
  const [topicsRaw, setTopicsRaw] = useState('');
  const [learningsRaw, setLearningsRaw] = useState('');
  const [kbSlugsRaw, setKbSlugsRaw] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setSummary('');
      setTranscript('');
      setTopicsRaw('');
      setLearningsRaw('');
      setKbSlugsRaw('');
      setErrorMsg(null);
    }
  }, [open]);

  const submit = async () => {
    setErrorMsg(null);
    if (!summary.trim() || !transcript.trim()) {
      setErrorMsg('Summary and transcript are required.');
      return;
    }
    const learnings = learningsRaw
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)
      .map((text) => ({ text }));
    try {
      await end.mutateAsync({
        summary: summary.trim(),
        transcript_markdown: transcript,
        topic_list: splitCsv(topicsRaw),
        learnings,
        kb_slugs: splitCsv(kbSlugsRaw),
      });
      onClose();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError
          ? describeTalksError(err.code, `HTTP ${err.status}`)
          : String(err),
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>End talk {talkId}</DialogTitle>
          <DialogDescription className="sr-only">
            Record summary, transcript markdown, topics, learnings, and KB references.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <FormField label="Summary" htmlFor={`${idBase}-summary`}>
            <input
              id={`${idBase}-summary`}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              className="input"
              autoFocus
            />
          </FormField>
          <FormField label="Transcript (markdown)" htmlFor={`${idBase}-tx`}>
            <Textarea
              id={`${idBase}-tx`}
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              rows={10}
            />
          </FormField>
          <FormField label="Topics (comma-separated, optional)" htmlFor={`${idBase}-topics`}>
            <input
              id={`${idBase}-topics`}
              value={topicsRaw}
              onChange={(e) => setTopicsRaw(e.target.value)}
              className="input"
            />
          </FormField>
          <FormField label="Learnings (one per line, optional)" htmlFor={`${idBase}-learn`}>
            <Textarea
              id={`${idBase}-learn`}
              value={learningsRaw}
              onChange={(e) => setLearningsRaw(e.target.value)}
              rows={4}
            />
          </FormField>
          <FormField label="KB slugs (comma-separated, optional)" htmlFor={`${idBase}-kb`}>
            <input
              id={`${idBase}-kb`}
              value={kbSlugsRaw}
              onChange={(e) => setKbSlugsRaw(e.target.value)}
              className="input"
            />
          </FormField>
        </div>
        {errorMsg && <p className="text-danger text-sm">{errorMsg}</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            disabled={!summary.trim() || !transcript.trim() || end.isPending}
            onClick={submit}
          >
            {end.isPending ? 'Closing…' : 'End talk'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
