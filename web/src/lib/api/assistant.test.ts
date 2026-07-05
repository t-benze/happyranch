import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as assistant from './assistant';
import * as clientModule from './client';

describe('assistant api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('getAssistantStatus GETs /assistant/status', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ state: 'uninitialized' });
    await assistant.getAssistantStatus();
    expect(spy).toHaveBeenCalledWith('/assistant/status');
  });

  it('initAssistant POSTs the reconfigure flag', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ state: 'uninitialized' });
    await assistant.initAssistant({ reconfigure: true });
    expect(spy).toHaveBeenCalledWith('/assistant/init', {
      method: 'POST',
      body: { reconfigure: true },
    });
  });

  it('registerAssistant POSTs the executor payload', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ state: 'configured' });
    await assistant.registerAssistant({
      executor: 'claude',
      command: 'claude',
      argv: ['claude'],
    });
    expect(spy).toHaveBeenCalledWith('/assistant/register', {
      method: 'POST',
      body: { executor: 'claude', command: 'claude', argv: ['claude'] },
    });
  });

  it('repairAssistant POSTs /assistant/repair with no body', async () => {
    const spy = vi
      .spyOn(clientModule, 'request')
      .mockResolvedValue({ state: 'configured' });
    await assistant.repairAssistant();
    expect(spy).toHaveBeenCalledWith('/assistant/repair', { method: 'POST' });
  });

  it('assistantBearerSubprotocol prefixes the token', () => {
    expect(assistant.assistantBearerSubprotocol('tok-123')).toBe(
      'happyranch.bearer.tok-123',
    );
  });});
