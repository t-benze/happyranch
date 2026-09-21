import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { AppRoutes } from '@/routes';
import { renderWithProviders } from '@/test/render';
import { server } from '@/test/server';

const SLUG = 'alpha';
const API = `/api/v1/orgs/${SLUG}/custom-skills`;
const CREATE_RESULT = {
  skill_id: 'custom:new',
  version_id: 1,
  content_hash: 'hash',
  validation_state: 'valid',
  hidden_reason: 'no_eligibility_policy',
};

function mount(): void {
  sessionStorage.setItem('happyranch.token', 'tok');
  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/skills/custom/new` });
}

function installShellHandlers(): void {
  server.use(
    http.get('/api/v1/orgs', () => HttpResponse.json({ orgs: [{ slug: SLUG, root: '/tmp/alpha' }] })),
    http.get(`/api/v1/orgs/${SLUG}/dashboard/summary`, () => HttpResponse.json({})),
  );
}

async function fill(
  user: ReturnType<typeof userEvent.setup>,
  options: { description?: string | null } = {},
): Promise<void> {
  const form = document.querySelector('form');
  expect(form).not.toBeNull();
  const scope = within(form as HTMLElement);
  await user.type(scope.getByLabelText(/^Name/), 'My Workflow');
  await user.type(scope.getByLabelText(/^Slug/), 'my-workflow');
  if (options.description != null) {
    await user.type(scope.getByLabelText(/^Description/), options.description);
  }
  await user.type(
    scope.getByLabelText(/^SKILL\.md/),
    '---\nname: my-workflow\ndescription: from frontmatter\n---\n',
  );
}

beforeEach(installShellHandlers);
afterEach(() => sessionStorage.clear());

describe('CustomSkillCreatePage (THR-262 description payload)', () => {
  test('a trimmed-blank optional description is omitted from the request', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post(API, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(CREATE_RESULT, { status: 201 });
      }),
    );
    mount();
    const user = userEvent.setup();
    await fill(user, { description: null });

    // The derived-description help is rendered.
    expect(
      screen.getByText(/Leave blank to use the description from the skill guide frontmatter/i),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Create custom skill/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).not.toHaveProperty('description');
    expect(captured).toMatchObject({
      name: 'My Workflow',
      slug: 'my-workflow',
      skill_md: expect.stringContaining('from frontmatter'),
    });
  });

  test('a whitespace-only description is trimmed blank and omitted', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post(API, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(CREATE_RESULT, { status: 201 });
      }),
    );
    mount();
    const user = userEvent.setup();
    await fill(user, { description: '   ' });
    await user.click(screen.getByRole('button', { name: /Create custom skill/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    // A trimmed-blank optional field is omitted so the server derives the
    // catalog description from the validated frontmatter.
    expect(captured).not.toHaveProperty('description');
  });

  test('a divergent nonblank description surfaces the candidate 422 error', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post(API, async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        // The candidate daemon contract for a nonblank description that differs
        // from the validated frontmatter: 422 divergent_description.
        return HttpResponse.json(
          {
            detail: {
              code: 'divergent_description',
              detail:
                'The supplied description does not match the validated frontmatter description',
            },
          },
          { status: 422 },
        );
      }),
    );
    mount();
    const user = userEvent.setup();
    await fill(user, { description: 'explicit copy' });
    await user.click(screen.getByRole('button', { name: /Create custom skill/i }));
    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      name: 'My Workflow',
      slug: 'my-workflow',
      description: 'explicit copy',
      skill_md: expect.stringContaining('description: from frontmatter'),
    });

    // The page renders the error state instead of navigating on success.
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Could not create this custom skill/i);
  });

  test('a 422 invalid_slug response surfaces the ASCII identity message', async () => {
    server.use(
      http.post(API, () =>
        HttpResponse.json(
          {
            detail: {
              code: 'invalid_slug',
              detail:
                'HappyRanch logical skill slugs must be 1-64 characters of ASCII lower-case letters, ASCII digits and single hyphens (a-z, 0-9, \'-\')...',
            },
          },
          { status: 422 },
        ),
      ),
    );
    mount();
    const user = userEvent.setup();
    await fill(user);
    await user.click(screen.getByRole('button', { name: /Create custom skill/i }));

    // The seq43 Option A request-identity refusal surfaces through the
    // existing error flow with plain-language ASCII grammar copy.
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/lower-case ASCII letters/i);
    expect(alert).not.toHaveTextContent(/Could not create this custom skill/i);
  });
});
