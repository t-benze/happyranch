import Foundation

/// Transport and owner-receipt facade. Settings/Connection actions remain later Mac slices.
enum AppSchemeScripts {
    static let page = #"""
(() => {
  if (window !== top || location.origin !== 'happyranch-app://app') return;
  const native = window.webkit.messageHandlers.appTransport;
  // Each injected relay belongs to one document. Native releases its calls
  // only after checking this loaded Main context; retirement revokes it there.
  const context = Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  let admit;
  const admitted = new Promise(resolve => { admit = resolve; });
  Object.defineProperty(window, '__hrAppContext', {value: context});
  Object.defineProperty(window, '__hrAppAdmit', {value: token => {
    if (token !== context) return false;
    admit(); return true;
  }});
  const listeners = new Map();
  Object.defineProperty(window, '__hrAppState', {value: raw => {
    for (const handler of listeners.get('app.stateChanged') || []) handler(raw);
  }});
  Object.defineProperty(window, 'happyranchConnection', {value: Object.freeze({
    call: async (command, data) => {
      await admitted;
      if (command === 'app.ownersChanged' && typeof data === 'string') {
        return native.postMessage({context, command, data});
      }
      if (command !== 'app.status' || data !== '{}') throw new Error('unavailable');
      return native.postMessage({context, command:'app.status'});
    },
    on: (event, handler) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(handler);
      return () => listeners.get(event)?.delete(handler);
    }
  })});
  const original = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const request = new Request(input, init), url = new URL(request.url);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/v1/')) return original(input, init);
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    const id = `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`, headers = new Headers(request.headers), signal = request.signal;
    headers.set('X-HappyRanch-Request-ID', id);
    headers.delete('X-HappyRanch-Body-Bytes');
    let sent = false, bodyReader;
    const abort = () => {
      bodyReader?.cancel().catch(() => {});
      if (sent) native.postMessage({context, command:'cancel', id}).catch(() => {});
    };
    const cleanup = () => signal.removeEventListener('abort', abort);
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    signal.addEventListener('abort', abort, {once:true});
    const timer = setTimeout(() => bodyReader?.cancel().catch(() => {}), 10000);
    try {
      let body;
      if (request.body) {
        bodyReader = request.body.getReader();
        const chunks = [], deadline = performance.now() + 10000;
        let size = 0;
        while (true) {
          const part = await bodyReader.read();
          if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
          if (performance.now() >= deadline) throw new TypeError('Body timed out');
          if (part.done) break;
          size += part.value.byteLength;
          if (size > 67108864) throw new TypeError('Body too large');
          chunks.push(part.value);
        }
        body = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
      }
      if (!['GET','HEAD'].includes(request.method)) headers.set('X-HappyRanch-Body-Bytes','1');
      clearTimeout(timer);
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      sent = true;
      const response = await original(request.url, {method:request.method, headers, body, signal,
        credentials:'omit', redirect:'error', cache:'no-store'});
      if (!response.body) { cleanup(); return response; }
      const reader = response.body.getReader();
      return new Response(new ReadableStream({
        async pull(controller) {
          try {
            const part = await reader.read();
            if (part.done) { cleanup(); controller.close(); } else controller.enqueue(part.value);
          } catch (error) { cleanup(); controller.error(error); }
        },
        cancel() { abort(); cleanup(); return reader.cancel(); }
      }), {status:response.status, statusText:response.statusText, headers:response.headers});
    } catch (error) { abort(); cleanup(); throw error; }
    finally { clearTimeout(timer); }
  };
})();
"""#
}
