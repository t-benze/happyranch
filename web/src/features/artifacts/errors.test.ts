import { describe, expect, test } from 'vitest';
import { translate } from '@/lib/i18n';
import { classifyArtifactError, renderArtifactError } from './validation';
const cases: Array<[string, unknown, string, string]> = [
  ['known size code', { code: 'artifact_too_large' }, 'File exceeds the 10 MB limit.', '文件超过 10 MB 限制。'],
  ['known missing artifact', { code: 'artifact_not_found' }, 'That artifact no longer exists.', '该共享文件已不存在。'],
  ['unknown code', { code: 'UNKNOWN_RAW', detail: 'ignored' }, 'UNKNOWN_RAW', 'UNKNOWN_RAW'],
  ['raw catalog-equal detail', { code: '', detail: 'Upload failed.' }, 'Upload failed.', 'Upload failed.'],
  ['raw HTTP detail', { detail: 'HTTP 503' }, 'HTTP 503', 'HTTP 503'],
  ['raw Error', new Error(' Raw failure '), ' Raw failure ', ' Raw failure '],
  ['raw string', ' Raw thrown ', ' Raw thrown ', ' Raw thrown '],
  ['blank detail fallback', { detail: '', message: 'API 500' }, 'Upload failed.', '上传失败。'],
  ['absent fallback', null, 'Upload failed.', '上传失败。'],
];
describe('artifact F1 error boundary', () => {
  test.each(cases)('%s renders in both locales without translating diagnostics', (_name, input, english, chinese) => {
    const view = classifyArtifactError(input, 'artifacts.error.upload');
    expect(renderArtifactError(view, (key, params) => translate('en', key, params))).toBe(english);
    expect(renderArtifactError(view, (key, params) => translate('zh-CN', key, params))).toBe(chinese);
  });
});
