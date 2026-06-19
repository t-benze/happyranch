import { describe, it, expect } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { render, screen } from '@testing-library/react';
import { LinkifyId } from './LinkifyId';

function renderLinkify(text: string, slug = 'testorg', prUrls?: Record<string, string>) {
  return render(
    <MemoryRouter>
      <LinkifyId text={text} slug={slug} prUrls={prUrls} />
    </MemoryRouter>,
  );
}

describe('LinkifyId', () => {
  it('renders plain text unchanged when no tokens present', () => {
    renderLinkify('hello world');
    expect(screen.getByText('hello world')).toBeDefined();
  });

  it('links THR- tokens', () => {
    renderLinkify('thread THR-042 was opened');
    const link = screen.getByText('THR-042');
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('/orgs/testorg/threads/THR-042');
  });

  it('links TASK- tokens', () => {
    renderLinkify('task TASK-099 completed');
    const link = screen.getByText('TASK-099');
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('/orgs/testorg/tasks/TASK-099');
  });

  it('links JOB- tokens', () => {
    renderLinkify('job JOB-007 running');
    const link = screen.getByText('JOB-007');
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('/orgs/testorg/jobs/JOB-007');
  });

  it('renders PR#N as plain text without stored URL', () => {
    renderLinkify('PR#121 merged');
    const span = screen.getByText('PR#121');
    expect(span.tagName).toBe('SPAN');
  });

  it('links PR#N when stored PR URL is provided', () => {
    renderLinkify('PR#121 merged', 'testorg', { 'PR#121': 'https://github.com/foo/bar/pull/121' });
    const link = screen.getByText('PR#121');
    expect(link.tagName).toBe('A');
    expect(link.getAttribute('href')).toBe('https://github.com/foo/bar/pull/121');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('renders PR#N as plain text when PR URLs map exists but lacks the token', () => {
    renderLinkify('PR#200 not found', 'testorg', { 'PR#121': 'https://example.com/121' });
    const span = screen.getByText('PR#200');
    expect(span.tagName).toBe('SPAN');
  });

  it('links multiple different tokens in one text', () => {
    renderLinkify('TASK-001 dispatched to THR-042 with JOB-003');
    const task = screen.getByText('TASK-001');
    const thread = screen.getByText('THR-042');
    const job = screen.getByText('JOB-003');
    expect(task.getAttribute('href')).toBe('/orgs/testorg/tasks/TASK-001');
    expect(thread.getAttribute('href')).toBe('/orgs/testorg/threads/THR-042');
    expect(job.getAttribute('href')).toBe('/orgs/testorg/jobs/JOB-003');
  });

  it('does not link lowercase or mid-word tokens', () => {
    renderLinkify('prefixTHR-042suffix and task-001');
    // Neither should be a link
    const links = screen.queryAllByRole('link');
    expect(links.length).toBe(0);
  });

  it('links tokens in leading position', () => {
    renderLinkify('TASK-001 was the first');
    const link = screen.getByText('TASK-001');
    expect(link.tagName).toBe('A');
  });

  it('links tokens in trailing position', () => {
    renderLinkify('the last one is TASK-001');
    const link = screen.getByText('TASK-001');
    expect(link.tagName).toBe('A');
  });

  it('handles empty string', () => {
    renderLinkify('');
    expect(screen.queryByRole('link')).toBeNull();
  });
});
