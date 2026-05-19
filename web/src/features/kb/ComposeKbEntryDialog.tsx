import { useId, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useAddKBEntry, useKbRoutes } from '@/hooks/kb';
import { KB_STRINGS } from './strings';

export function ComposeKbEntryDialog({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const routes = useKbRoutes();
  const mutation = useAddKBEntry();
  const slugId = useId();
  const titleId = useId();
  const typeId = useId();
  const topicId = useId();
  const tagsId = useId();
  const bodyId = useId();
  const sourceTaskId = useId();
  const relatedId = useId();
  const [slug, setSlug] = useState('');
  const [title, setTitle] = useState('');
  const [type, setType] = useState('');
  const [topic, setTopic] = useState('');
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [sourceTask, setSourceTask] = useState('');
  const [related, setRelated] = useState('');

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const splitCsv = (v: string) =>
      v
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
    const result = await mutation.mutateAsync({
      slug,
      title,
      type,
      topic,
      body,
      agent: 'founder',
      tags: splitCsv(tags),
      related_entries: splitCsv(related),
      source_task: sourceTask || undefined,
    });
    onClose();
    navigate(routes.detail(result.slug));
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{KB_STRINGS.composeDialogTitle}</DialogTitle>
        </DialogHeader>
        <form className="space-y-3" onSubmit={onSubmit}>
          <div>
            <Label htmlFor={slugId}>Slug</Label>
            <Input id={slugId} value={slug} onChange={(e) => setSlug(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={titleId}>Title</Label>
            <Input id={titleId} value={title} onChange={(e) => setTitle(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={typeId}>Type</Label>
            <Input id={typeId} value={type} onChange={(e) => setType(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={topicId}>Topic</Label>
            <Input id={topicId} value={topic} onChange={(e) => setTopic(e.target.value)} required />
          </div>
          <div>
            <Label htmlFor={tagsId}>Tags (comma-separated)</Label>
            <Input id={tagsId} value={tags} onChange={(e) => setTags(e.target.value)} />
          </div>
          <div>
            <Label htmlFor={bodyId}>Body (Markdown)</Label>
            <Textarea id={bodyId} value={body} onChange={(e) => setBody(e.target.value)} rows={8} required />
          </div>
          <div>
            <Label htmlFor={sourceTaskId}>Source task (optional, e.g. TASK-0042)</Label>
            <Input id={sourceTaskId} value={sourceTask} onChange={(e) => setSourceTask(e.target.value)} />
          </div>
          <div>
            <Label htmlFor={relatedId}>Related entries (comma-separated slugs)</Label>
            <Input id={relatedId} value={related} onChange={(e) => setRelated(e.target.value)} />
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              {KB_STRINGS.composeDialogCancel}
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {KB_STRINGS.composeDialogSubmit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
