// Fixed, audited relay shared in shape with the slice-1a/Linux facade.
// All native data uses WebKit reply values; no data enters executable source.
enum ConnectionScripts {
    static let page = #"""
(() => {
  'use strict';
  if (window !== top || location.href !== 'happyranch-shell://connection/index.html'
      || location.origin !== 'happyranch-shell://connection') return;
  const fail = code => JSON.stringify({code, effect:'none', recovery:'refresh'});
  const pending = new Map(), listeners = new Map();
  let sequence = 0, active = true;
  const size = text => typeof text === 'string' && new TextEncoder().encode(text).length;
  addEventListener('message', event => {
    if (!active || event.source !== window || event.origin !== location.origin) return;
    const item = event.data;
    if (!item || item.channel !== 'hr-connection-reply' || typeof item.id !== 'string') return;
    const callback = pending.get(item.id);
    if (!callback) return;
    pending.delete(item.id);
    try {
      if (!size(item.raw) || size(item.raw) > 32768) throw 0;
      const reply = JSON.parse(item.raw);
      if (reply.protocol !== 'happyranch.connection' || reply.version !== 1
          || reply.requestId !== item.id || typeof reply.ok !== 'boolean') throw 0;
      if (reply.ok) callback.resolve(JSON.stringify(reply.result));
      else callback.reject(JSON.stringify(reply.error));
    } catch { callback.reject(fail('invalidRequest')); }
  });
  const call = (command, data) => new Promise((resolve, reject) => {
    if (!active) return reject(fail('documentChanged'));
    if (typeof command !== 'string' || typeof data !== 'string' || size(data) > 4096)
      return reject(fail('invalidRequest'));
    if (pending.size >= 8) return reject(fail('busy'));
    const id = 'r' + (++sequence);
    pending.set(id, {resolve, reject});
    window.postMessage({channel:'hr-connection-call', id, command, data}, location.origin);
  });
  const on = (name, handler) => {
    if (!['host.stateChanged','client.stateChanged'].includes(name) || typeof handler !== 'function')
      throw fail('unsupportedCommand');
    if (!active) throw fail('documentChanged');
    if (!listeners.has(name)) listeners.set(name, new Set());
    const group = listeners.get(name); group.add(handler);
    return () => group.delete(handler);
  };
  addEventListener('pagehide', () => {
    active = false; listeners.clear();
    pending.forEach(item => item.reject(fail('documentChanged'))); pending.clear();
  }, {once:true});
  Object.defineProperty(window, 'happyranchConnection', {value:Object.freeze({call,on})});
})();
"""#
    static let relay = #"""
(() => {
  'use strict';
  if (window !== top || location.href !== 'happyranch-shell://connection/index.html'
      || location.origin !== 'happyranch-shell://connection') return;
  const native = window.webkit.messageHandlers.connection;
  let active = true;
  const revoke = () => {
    if (!active) return;
    active = false;
    // Non-string input is always rejected. This notification can only revoke
    // the current document, never authorize a command or supply frame identity.
    native.postMessage(null).catch(() => {});
  };
  const embedded = node => node.nodeType === 1 &&
    (node.matches('iframe,frame,object,embed') || node.querySelector('iframe,frame,object,embed'));
  const observer = new MutationObserver(records => {
    if (records.some(record => Array.from(record.addedNodes).some(embedded))) revoke();
  });
  observer.observe(document, {childList:true, subtree:true});
  addEventListener('securitypolicyviolation', event => {
    if (['frame-src','child-src'].includes(event.effectiveDirective)) revoke();
  });
  addEventListener('pagehide', () => { active = false; observer.disconnect(); }, {once:true});
  addEventListener('message', async event => {
    if (!active || event.source !== window || event.origin !== 'happyranch-shell://connection') return;
    const item = event.data;
    if (!item || item.channel !== 'hr-connection-call' || typeof item.data !== 'string'
        || typeof item.command !== 'string' || !/^r[0-9]{1,16}$/.test(item.id)) return;
    // Keep args text intact: native must see duplicate keys and bad JSON too.
    const raw = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,command:item.command}).slice(0,-1) + ',"args":' + item.data + '}';
    let reply;
    try { reply = await native.postMessage(raw); }
    catch { reply = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,ok:false,error:{code:'unavailable',effect:'none',recovery:'refresh'}}); }
    if (typeof reply === 'string' && new TextEncoder().encode(reply).length <= 32768)
      window.postMessage({channel:'hr-connection-reply',id:item.id,raw:reply}, location.origin);
  });
})();
"""#
    static let probe = #"""
return window === top && location.origin === 'happyranch-shell://connection'
    && location.href === 'happyranch-shell://connection/index.html'
    && typeof window.webkit?.messageHandlers?.connection?.postMessage === 'function'
    && window.frames.length === 0;
"""#
}
