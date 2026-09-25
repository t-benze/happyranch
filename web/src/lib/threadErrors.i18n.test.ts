/**
 * THR-118 W3a — locale-neutral thread error descriptors.
 *
 * A mapped daemon code renders through the catalog in the active locale; an
 * unmapped code (`HTTP <status>`), a non-ApiError and an empty or
 * catalog-byte-equal raw diagnostic stay byte-for-byte.
 */
import { describe, expect, test } from 'vitest';
import { ApiError } from '@/lib/api/client';
import { translate, type MessageKey, type MessageParams } from '@/lib/i18n';
import { MAX_THREAD_ATTACHMENTS } from './threadAttachments';
import {
  THREAD_ERROR_KEYS,
  THREAD_ERROR_STRINGS,
  classifyThreadError,
  renderThreadError,
} from './threadErrors';

const tEn = (k: MessageKey, p?: MessageParams) => translate('en', k, p);
const tZh = (k: MessageKey, p?: MessageParams) => translate('zh-CN', k, p);

describe('classifyThreadError / renderThreadError', () => {
  test('every legacy mapped code has a catalog key whose English is byte-identical', () => {
    expect(Object.keys(THREAD_ERROR_KEYS).sort()).toEqual(Object.keys(THREAD_ERROR_STRINGS).sort());
    for (const code of Object.keys(THREAD_ERROR_STRINGS)) {
      const detail = classifyThreadError(new ApiError(400, code, null));
      expect(detail.kind).toBe('mapped');
      expect(renderThreadError({ detail }, tEn)).toBe(THREAD_ERROR_STRINGS[code]);
      expect(renderThreadError({ detail }, tZh)).not.toBe(THREAD_ERROR_STRINGS[code]);
    }
  });

  test('too_many_attachments carries the max as a named param in both locales', () => {
    const detail = classifyThreadError(new ApiError(400, 'too_many_attachments', null));
    expect(renderThreadError({ detail }, tZh)).toContain(String(MAX_THREAD_ATTACHMENTS));
  });

  test('the same descriptor re-renders per locale (no stored string)', () => {
    const view = {
      label: { key: 'threads.error.notFound' as MessageKey },
      detail: classifyThreadError(new ApiError(404, 'not_found', null)),
    };
    expect(renderThreadError(view, tEn)).toBe('Thread not found.Thread not found.');
    expect(renderThreadError(view, tZh)).toBe('未找到会话。未找到会话。');
  });

  test('unmapped code, null code and non-ApiError stay raw and exact', () => {
    expect(classifyThreadError(new ApiError(500, 'weird_code', null))).toEqual({ kind: 'raw', text: 'HTTP 500' });
    expect(classifyThreadError(new ApiError(502, null, null))).toEqual({ kind: 'raw', text: 'HTTP 502' });
    const catalogEqual = new Error(THREAD_ERROR_STRINGS.not_found);
    const detail = classifyThreadError(catalogEqual);
    expect(detail.kind).toBe('raw');
    expect(renderThreadError({ detail }, tZh)).toBe(`Error: ${THREAD_ERROR_STRINGS.not_found}`);
    expect(renderThreadError({ detail: classifyThreadError('') }, tZh)).toBe('');
    expect(renderThreadError({ detail: { kind: 'raw', text: 'Thread not found.' } }, tZh)).toBe('Thread not found.');
  });
});
