import { byteLength, closedError, encodeRequest, events, parseArgs, parseError, parseEvent, parseHello, parseResult } from './protocol';
import type { Hello, Json } from './protocol';

export interface Facade {
  call(command: string, data: string): Promise<string>;
  on(eventName: string, handler: (data: string) => void): () => void;
}
declare global { interface Window { happyranchConnection?: Facade } }

/** UX adapter only. View/document admission, correlation and custody remain native. */
export class Connection {
  readonly hello: Hello;
  private active = true;
  private reads = 0;
  private mutation = false;
  private detach = new Set<() => void>();
  private sequences = new Map<string, number>();
  private streams = new Map<string, { listeners: Set<(data: string) => void>; stop: () => void; sequence: number }>();
  private constructor(private facade: Facade, hello: Hello, private invalid: () => void) { this.hello = hello; }

  static async detect(facade: Facade | undefined, invalid: () => void): Promise<Connection | null> {
    if (!facade || typeof facade.call !== 'function' || typeof facade.on !== 'function') return null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const raw = await Promise.race([
        Promise.resolve().then(() => facade.call('bridge.hello', '{"minVersion":1,"maxVersion":1}')),
        new Promise<never>((_, reject) => { timer = setTimeout(() => reject(closedError('unavailable')), 1000); }),
      ]);
      return new Connection(facade, parseHello(raw), invalid);
    } catch { return null; } finally { clearTimeout(timer); }
  }
  has(command: string): boolean {
    return this.active && this.hello.capabilities.some((item) => item.command === command && item.availability === 'available');
  }
  async call(command: string, data: string): Promise<string> {
    parseArgs(command, data);
    // Reserve worst-case correlation overhead inside the 4 KiB envelope limit.
    encodeRequest(command, data, 'r'.repeat(64));
    if (!this.active) throw closedError('documentChanged');
    if (!this.has(command)) throw closedError('unsupportedCommand');
    const read = ['host.status', 'client.status', 'client.savedComputers', 'client.health'].includes(command);
    const cancellation = command === 'client.cancelAttempt';
    if ((read && this.reads >= 8) || (!read && !cancellation && this.mutation)) throw closedError('busy');
    if (read) this.reads++; else if (!cancellation) this.mutation = true;
    try {
      let raw: unknown;
      try { raw = await this.facade.call(command, data); }
      catch (error) {
        if (!this.active) throw closedError('documentChanged', 'unknown', 'refresh');
        try { parseError(error); } catch { this.fail(); throw closedError('outcomeUnknown', 'unknown', 'refresh'); }
        throw error;
      }
      if (!this.active) throw closedError('documentChanged', 'unknown', 'refresh');
      try { parseResult(command, raw); } catch { this.fail(); throw closedError('invalidRequest', 'unknown', 'refresh'); }
      return raw as string;
    } finally { if (read) this.reads--; else if (!cancellation) this.mutation = false; }
  }
  on(eventName: string, handler: (data: string) => void): () => void {
    if (!events.includes(eventName)) throw closedError('unsupportedCommand');
    if (!this.active) throw closedError('documentChanged');
    const role = eventName.startsWith('host.') ? 'host' : 'client';
    if (!this.hello.roles.includes(role)) throw closedError('unsupportedCommand');
    let stream = this.streams.get(eventName);
    if (!stream) {
      stream = { listeners: new Set(), stop: () => {}, sequence: this.sequences.get(eventName) ?? 0 };
      this.streams.set(eventName, stream);
      const current = stream;
      // Add the first listener before native's initial full snapshot can arrive.
      current.listeners.add(handler);
      try {
        const stop = this.facade.on(eventName, (raw) => {
          if (!this.active || this.streams.get(eventName) !== current) return;
          try {
            if (byteLength(raw) > 32768) throw closedError('invalidRequest');
            const data = parseEvent(eventName, raw) as Record<string, Json>;
            if ((data.sequence as number) <= current.sequence) throw closedError('stateChanged');
            current.sequence = data.sequence as number;
            this.sequences.set(eventName, current.sequence);
          } catch { this.fail(); return; }
          current.listeners.forEach((listener) => listener(raw));
        });
        if (typeof stop !== 'function') throw closedError('invalidRequest');
        current.stop = stop;
        if (!this.active) { stop(); throw closedError('documentChanged'); }
      } catch { this.fail(); throw closedError('unavailable'); }
    } else stream.listeners.add(handler);
    let subscribed = true;
    const unsubscribe = (): void => {
      if (!subscribed) return;
      subscribed = false;
      this.detach.delete(unsubscribe);
      stream.listeners.delete(handler);
      if (stream.listeners.size === 0 && this.streams.get(eventName) === stream) {
        this.streams.delete(eventName);
        try { stream.stop(); } catch { /* Never reflect native exceptions. */ }
      }
    };
    this.detach.add(unsubscribe);
    return unsubscribe;
  }
  dispose(): void {
    this.active = false;
    this.detach.forEach((stop) => stop());
    this.streams.forEach((stream) => { try { stream.stop(); } catch { /* Closed teardown. */ } });
    this.streams.clear();
  }
  private fail(): void { this.dispose(); this.invalid(); }
}
