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
import { Textarea } from '@/design-system/primitives/Textarea';
import { FormField } from '@/design-system/patterns/FormField';
import { ApiError } from '@/lib/api';
import { useAddKBEntry, useKbRoutes } from '@/hooks/kb';
import { KB_STRINGS, describeError } from './strings';

export function ComposeKbEntryDialog({
  onClose,
}: {
  onClose: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const routes = useKbRoutes();
  const mutation = useAddKBEntry();

  const idBase = useId();
  const slugId = `${idBase}-slug`;
  const titleId = `${idBase}-title`;
  const typeId = `${idBase}-type`;
  const topicId = `${idBase}-topic`;
  const tagsId = `${idBase}-tags`;
  const bodyId = `${idBase}-body`;
  const sourceTaskId = `${idBase}-source-task`;
  const relatedId = `${idBase}-related`;

  const [slug, setSlug] = useState('');
  const [title, setTitle] = useState('');
  const [type, setType] = useState('');
  const [topic, setTopic] = useState('');
  const [tags, setTags] = useState('');
  const [body, setBody] = useState('');
  const [sourceTask, setSourceTask] = useState('');
  const [related, setRelated] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    const splitCsv = (v: string) =>
      v
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
    try {
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
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? describeError(err.code, `HTTP ${err.status}`) : String(err),
      );
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{KB_STRINGS.composeDialogTitle}</DialogTitle>
        </DialogHeader>
        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <FormField label="Slug" htmlFor={slugId}>
            <Input
              id={slugId}
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              required
            />
          </FormField>
          <FormField label="Title" htmlFor={titleId}>
            <Input
              id={titleId}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </FormField>
          <FormField label="Type" htmlFor={typeId}>
            <Input
              id={typeId}
              value={type}
              onChange={(e) => setType(e.target.value)}
              required
            />
          </FormField>
          <FormField label="Topic" htmlFor={topicId}>
            <Input
              id={topicId}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              required
            />
          </FormField>
          <FormField label="Tags (comma-separated)" htmlFor={tagsId}>
            <Input
              id={tagsId}
              value={tags}
              onChange={(e) => setTags(e.target.value)}
            />
          </FormField>
          <FormField label="Body (Markdown)" htmlFor={bodyId}>
            <Textarea
              id={bodyId}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={8}
              required
            />
          </FormField>
          <FormField
            label="Source task (optional, e.g. TASK-0042)"
            htmlFor={sourceTaskId}
          >
            <Input
              id={sourceTaskId}
              value={sourceTask}
              onChange={(e) => setSourceTask(e.target.value)}
            />
          </FormField>
          <FormField
            label="Related entries (comma-separated slugs)"
            htmlFor={relatedId}
          >
            <Input
              id={relatedId}
              value={related}
              onChange={(e) => setRelated(e.target.value)}
            />
          </FormField>
          {errorMsg && <p className="text-feedback-danger text-xs">{errorMsg}</p>}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={onClose}>
              {KB_STRINGS.composeDialogCancel}
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending
                ? KB_STRINGS.composeDialogSubmitting
                : KB_STRINGS.composeDialogSubmit}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
