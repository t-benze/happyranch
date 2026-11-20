// Throwaway browser observations; no real daemon mutation or remote dialing.
import { fetchEventSource } from '/probe/fetch-event-source.js';
const report = (name, detail) => window.webkit.messageHandlers.probe.postMessage(JSON.stringify({name, detail}));
const check = async (name, work) => {
  try { report(name, await work()); } catch (e) { report(name, {error: String(e)}); }
};
const omitMultipart = new URL(location.href).searchParams.has('omitMultipart');
const serializeMultipart = new URL(location.href).searchParams.has('serializeMultipart');
const directOnly = new URL(location.href).searchParams.has('directOnly');
window.addEventListener('error', e => report('page-error', e.message));
report('document', {url: location.href, origin: location.origin, secure: isSecureContext});
await check('storage', async () => {
  const previous = localStorage.getItem('s0');
  localStorage.setItem('s0', 'persistent-s0');
  const db = await new Promise((resolve, reject) => {
    const r = indexedDB.open('s0', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error);
  });
  const old = await new Promise((resolve, reject) => {
    const t = db.transaction('kv', 'readwrite'), s = t.objectStore('kv');
    const r = s.get('s0'); let value;
    r.onsuccess = () => { value = r.result; s.put('persistent-s0', 's0'); };
    t.oncomplete = () => resolve(value ?? null); t.onerror = () => reject(t.error);
  });
  db.close(); return {localStorage: previous, indexedDB: old};
});
await check('history', async () => {
  history.pushState({}, '', '/settings');
  return {path: location.pathname, origin: location.origin};
});
// Native reloads /settings in a second process; no sessionStorage prerequisite.
for (const method of ['POST', 'PUT']) await check(method, async () => {
  const body = JSON.stringify({hello: '你好', n: 42});
  const r = await fetch('/probe/body', {method, headers: {'Content-Type':'application/json'}, body});
  const result = await r.json(); return {...result, intact: result.text === body};
});
if (!omitMultipart) await check('multipart', async () => {
  const form = new FormData(); form.append('file', new Blob(['s'.repeat(10 * 1024 * 1024)]), 's0.bin');
  // Separate workaround observation; the default FormData case stays unchanged.
  const encoded = serializeMultipart ? new Response(form) : null;
  const result = await (await fetch('/probe/body', encoded ? {
    method:'POST', headers:{'Content-Type':encoded.headers.get('Content-Type')},
    body:new Uint8Array(await encoded.arrayBuffer()),
  } : {method: 'POST', body: form})).json();
  return {...result, encoding:serializeMultipart ? 'serialized-bytes' : 'FormData'};
});
await check('stream', async () => {
  const controller = new AbortController(), chunks = [], start = performance.now();
  await fetchEventSource('/probe/stream', {
    signal: controller.signal, openWhenHidden: true,
    onmessage(e) { chunks.push({receivedMs: performance.now() - start, receivedWallMs:Date.now(), ...JSON.parse(e.data)}); },
    onerror(e) { throw e; },
  });
  return chunks;
});
await check('abort', async () => {
  const controller = new AbortController(), start = performance.now(); let count = 0, abortedWallMs;
  const timer = setTimeout(() => controller.abort(), 2500);
  await fetchEventSource('/probe/cancel', {
    signal: controller.signal, openWhenHidden: true,
    onmessage() { if (++count === 2) { abortedWallMs = Date.now(); controller.abort(); } }, onerror(e) { throw e; },
  });
  clearTimeout(timer); return {count, elapsedMs: performance.now() - start, abortedWallMs};
});
await check('daemon-health', async () => {
  const r = await fetch('/api/v1/health'); const j = await r.json();
  return {status: r.status, healthy: j.status === 'ok'};
});
await check('daemon-stream', async () => {
  const controller = new AbortController(), messages = [];
  const timer = setTimeout(() => controller.abort(), 4500);
  await fetchEventSource('/api/v1/orgs/happyranch/threads/THR-387/tail?since_seq=0', {
    signal: controller.signal, openWhenHidden: true,
    onmessage(e) { messages.push({event:e.event, length:e.data.length}); controller.abort(); },
    onerror(e) { throw e; },
  });
  clearTimeout(timer); return messages;
});
if (!directOnly) await check('download-fetch', async () => {
  const r = await fetch('/probe/download');
  const blob = await r.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 's0-blob.txt'; a.click();
  return {status:r.status, disposition:r.headers.get('Content-Disposition'), text:await blob.text()};
});
const a = document.createElement('a'); a.href = '/probe/download'; a.click();
report('done', true);
