// Focused Node 24 checks; no Vitest or Product E2E selection.
// Run from the repository root: node --test web/scripts/multipart.test.mjs
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { prepareMultipartUpload } from '../src/lib/api/multipart.ts';

const originalLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
after(() => {
  if (originalLocation) Object.defineProperty(globalThis, 'location', originalLocation);
  else delete globalThis.location;
});
function protocol(value) {
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { protocol: value } });
}
const tooLarge = new Error('existing caller error');

test('scheme multipart has byte-exact CRLF framing, escaped names and intact binary payload', async () => {
  protocol('happyranch-app:');
  const bytes = Uint8Array.from({ length: 256 }, (_, i) => i);
  const form = new FormData();
  form.append('n"o\r\nte', 'first\nsecond\rthird\r\nlast');
  form.append('file', new File([bytes], 'a"b\r\nc.txt', { type: 'application/octet-stream' }));
  const { body, headers } = await prepareMultipartUpload(form, tooLarge);
  assert.ok(body instanceof Uint8Array);
  const match = /^multipart\/form-data; boundary=([A-Za-z0-9-]+)$/.exec(headers['Content-Type']);
  assert.ok(match, 'explicit multipart boundary');
  const boundary = match[1];
  const expected = Buffer.concat([
    Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="n%22o%0D%0Ate"\r\n\r\nfirst\r\nsecond\r\nthird\r\nlast\r\n`),
    Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="a%22b%0D%0Ac.txt"\r\nContent-Type: application/octet-stream\r\n\r\n`),
    Buffer.from(bytes),
    Buffer.from(`\r\n--${boundary}--\r\n`),
  ]);
  assert.deepEqual(Buffer.from(body), expected);
  const parsed = await new Response(body, { headers }).formData();
  assert.equal(parsed.get('n"o\r\nte'), 'first\r\nsecond\r\nthird\r\nlast');
  const file = parsed.get('file');
  assert.equal(file.name, 'a"b\r\nc.txt');
  assert.equal(file.type, 'application/octet-stream');
  assert.deepEqual(new Uint8Array(await file.arrayBuffer()), bytes);
});

test('scheme encoding handles Unicode, repeated fields and empty files with fresh boundaries', async () => {
  protocol('happyranch-app:');
  const form = new FormData();
  form.append('标签', '你好');
  form.append('标签', 'second');
  form.append('file', new File([], '空.txt'));
  const first = await prepareMultipartUpload(form, tooLarge);
  const second = await prepareMultipartUpload(form, tooLarge);
  assert.notEqual(first.headers['Content-Type'], second.headers['Content-Type']);
  const parsed = await new Response(first.body, { headers: first.headers }).formData();
  assert.deepEqual(parsed.getAll('标签'), ['你好', 'second']);
  assert.equal(parsed.get('file').name, '空.txt');
  assert.equal(parsed.get('file').size, 0);
  assert.equal(parsed.get('file').type, 'application/octet-stream');
});

test('scheme accepts the 10 MiB cap and rejects larger files before starting the encoder', async () => {
  protocol('happyranch-app:');
  const form = new FormData();
  form.append('file', new File([new Uint8Array(10 * 1024 * 1024)], 'limit.bin'));
  const upload = await prepareMultipartUpload(form, tooLarge);
  const parsed = await new Response(upload.body, { headers: upload.headers }).formData();
  assert.equal(parsed.get('file').size, 10 * 1024 * 1024);
  form.append('another', new File([new Uint8Array(10 * 1024 * 1024 + 1)], 'large.bin'));
  const originalResponse = globalThis.Response;
  globalThis.Response = class { constructor() { assert.fail('oversize must fail before encoding'); } };
  try {
    await assert.rejects(prepareMultipartUpload(form, tooLarge), (error) => error === tooLarge);
  } finally {
    globalThis.Response = originalResponse;
  }
});

test('ordinary origins retain the original FormData and browser-owned Content-Type', async () => {
  const form = new FormData();
  form.set('file', new File(['browser bytes'], 'browser.txt'));
  const originalResponse = globalThis.Response;
  globalThis.Response = class { constructor() { assert.fail('ordinary path must not encode'); } };
  try {
    for (const value of ['http:', 'https:', 'file:']) {
      protocol(value);
      const upload = await prepareMultipartUpload(form, tooLarge);
      assert.equal(upload.body, form);
      assert.deepEqual(upload.headers, {});
    }
  } finally {
    globalThis.Response = originalResponse;
  }
});
