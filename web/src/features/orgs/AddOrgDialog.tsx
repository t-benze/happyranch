/**
 * Add Org dialog — opened from the TopBar.
 *
 * Slug-only form, posts to POST /api/v1/orgs. On success the orgs list
 * query is invalidated and the user navigates to `/orgs/<new>/threads`.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { orgs as orgsApi } from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddOrgDialog({ open, onOpenChange }: Props): JSX.Element {
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const create = useMutation({
    mutationFn: (body: { slug: string }) => orgsApi.createOrg(body),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['orgs'] });
      onOpenChange(false);
      navigate(`/orgs/${resp.slug}/threads`);
    },
    onError: (err: unknown) => {
      const e = err as { code?: string; status?: number; message?: string };
      if (e.code === 'no_active_runtime') {
        setServerError('No runtime is active yet — the daemon is still starting up. Try again in a moment.');
      } else if (e.code === 'org_exists' || e.code === 'org_dir_exists' || e.status === 409) {
        setServerError(`An org with slug "${slug}" already exists.`);
      } else if (e.code === 'invalid_slug') {
        setServerError('Slug must match ^[a-z0-9-]{1,40}$.');
      } else {
        setServerError(e.message ?? 'Could not create org.');
      }
    },
  });

  const valid = SLUG_RE.test(slug);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New org</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="org-slug">Slug</Label>
          <Input
            id="org-slug"
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setServerError(null);
            }}
            placeholder="e.g. hk-macau-tourism"
            autoFocus
          />
          <p className="text-fg-muted text-xs">
            Lowercase letters, digits, and hyphens. 1–40 characters.
          </p>
          {serverError && (
            <p className="text-tier-red text-sm">{serverError}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!valid || create.isPending}
            onClick={() => create.mutate({ slug })}
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
