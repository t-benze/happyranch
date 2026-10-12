import { describe, it, expect, beforeEach } from 'vitest';
import { webcrypto } from 'node:crypto';
import { exactBytes, base64, sha256, verifiedText, saveCommand, loadCommand, clearSelectionCommands } from './workflowReviewSelection';
import type { HumanReviewCommand } from './api/types';
beforeEach(() => { Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true }); sessionStorage.clear(); });
describe('U2/U3 exact UTF8 and replay journal through shipping helpers', () => {
  it('preserves CJK, composed/decomposed text and newlines; refuses noncanonical base64/digest/UTF8', async () => {
    const text = '\ufeff# 评审\nCafe\u0301\r\n<script>x</script>\n';
    const bytes = exactBytes(text), digest = await sha256(bytes);
    expect(await verifiedText(base64(bytes), digest)).toBe(text);
    await expect(verifiedText(base64(bytes) + '\n', digest)).rejects.toThrow();
    await expect(verifiedText(base64(bytes), '0'.repeat(64))).rejects.toThrow();
    const invalid = Uint8Array.from([0xff]);
    await expect(verifiedText(base64(invalid), await sha256(invalid))).rejects.toThrow();
    expect(() => exactBytes('\ud800')).toThrow(); expect(() => exactBytes('')).toThrow();
  });
  it('round-trips one exact body/key and refuses corrupt or extended envelopes', () => {
    const body: HumanReviewCommand = { format: 'workflow-human-review-command@3', action: 'sign-review', operation_key: 'human-review:one', request_id: 'request-one', expected_revision: 2,
      request_scope_digest: '1'.repeat(64), outcome: 'approved', findings_bytes_base64: 'eA==', findings_digest: '2'.repeat(64) };
    const identity = JSON.stringify(['org-a','activation-a','human','request-one',2,'1'.repeat(64)]);
    const e = saveCommand(identity, body);
    expect(loadCommand(identity)).toEqual(e); expect(JSON.parse(e.serialized)).toEqual(body);
    sessionStorage.setItem('happyranch.workflow.command@1:' + identity, JSON.stringify({ ...e, token: 'forbidden' }));
    expect(() => loadCommand(identity)).toThrow();
    clearSelectionCommands('org-a', 'activation-a'); expect(sessionStorage.length).toBe(0);
  });
});
