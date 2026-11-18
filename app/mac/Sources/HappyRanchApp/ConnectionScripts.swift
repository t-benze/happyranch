// Fixed, audited relay shared in shape with the slice-1a/Linux facade.
// All native data uses WebKit reply values; no data enters executable source.
enum ConnectionScripts {
    static let page = #"""
(() => {
  'use strict';
  if (window !== top || location.href !== 'happyranch-shell://connection/index.html'
      || location.origin !== 'happyranch-shell://connection') return;
  const fail = (code, effect = 'none', recovery = 'refresh') => JSON.stringify({code, effect, recovery});
  const pending = new Map(), listeners = new Map();
  let sequence = 0, active = true;
  const size = text => typeof text === 'string' && new TextEncoder().encode(text).length;
  addEventListener('message', event => {
    if (!active || event.source !== window || event.origin !== location.origin) return;
    const item = event.data;
    if (item?.channel === 'hr-connection-event' && item.name === 'client.stateChanged') {
      if (!size(item.raw) || size(item.raw) > 32768) return;
      try {
        const data = JSON.parse(item.raw);
        if (data.version !== 1 || !Number.isSafeInteger(data.sequence) || data.sequence < 1
            || typeof data.observedAt !== 'string' || !data.snapshot) return;
        listeners.get(item.name)?.forEach(handler => { try { handler(item.raw); } catch {} });
      } catch {}
      return;
    }
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
    } catch { callback.reject(fail('outcomeUnknown', 'unknown', 'explicitRetry')); }
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
    window.postMessage({channel:'hr-connection-subscription', active:true}, location.origin);
    let attached = true;
    return () => {
      if (!attached) return;
      attached = false; group.delete(handler);
      if (![...listeners.values()].some(value => value.size))
        window.postMessage({channel:'hr-connection-subscription', active:false}, location.origin);
    };
  };
  addEventListener('pagehide', () => {
    active = false; listeners.clear();
    pending.forEach(item => item.reject(fail('documentChanged', 'unknown', 'explicitRetry'))); pending.clear();
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
  Object.defineProperty(globalThis, '__hrConnectionEvent', {value: data => {
    if (active && typeof data === 'string')
      window.postMessage({channel:'hr-connection-event',name:'client.stateChanged',raw:data}, location.origin);
  }});
  const revoke = () => {
    if (!active) return;
    active = false;
    // Null input is always rejected. This notification can only revoke
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
    if (item?.channel === 'hr-connection-subscription' && typeof item.active === 'boolean') {
      native.postMessage(item.active).catch(() => {});
      return;
    }
    if (!item || item.channel !== 'hr-connection-call' || typeof item.data !== 'string'
        || typeof item.command !== 'string' || !/^r[0-9]{1,16}$/.test(item.id)) return;
    // Keep args text intact: native must see duplicate keys and bad JSON too.
    const raw = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,command:item.command}).slice(0,-1) + ',"args":' + item.data + '}';
    let reply;
    try { reply = await native.postMessage(raw); }
    catch { reply = JSON.stringify({protocol:'happyranch.connection',version:1,
      requestId:item.id,ok:false,error:{code:'outcomeUnknown',effect:'unknown',recovery:'explicitRetry'}}); }
    if (typeof reply === 'string' && new TextEncoder().encode(reply).length <= 32768)
      window.postMessage({channel:'hr-connection-reply',id:item.id,raw:reply}, location.origin);
  });
})();
"""#
    static let event = #"""
if (window === top && location.origin === 'happyranch-shell://connection'
    && location.href === 'happyranch-shell://connection/index.html' && window.frames.length === 0)
  globalThis.__hrConnectionEvent(data);
"""#
    static let probe = #"""
return window === top && location.origin === 'happyranch-shell://connection'
    && location.href === 'happyranch-shell://connection/index.html'
    && typeof window.webkit?.messageHandlers?.connection?.postMessage === 'function'
    && window.frames.length === 0;
"""#
}
