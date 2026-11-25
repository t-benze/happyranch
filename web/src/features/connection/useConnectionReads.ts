/**
 * Native reads for the Connection dialog (THR-387 S6-W3).
 *
 * Component state only: closing the dialog drops it, and reopening reads
 * native state again. Reads never mutate; no action is replayed. The newest
 * read wins (a revision fence), and reads triggered by `app.stateChanged`
 * are coalesced: at most one in flight plus one trailing read.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { AppState } from '@/lib/native/appStatus';
import {
  callConnection,
  failureOf,
  parseClientSnapshot,
  parseHealth,
  parseHostSnapshot,
  subscribeHostState,
  UNKNOWN_FAILURE,
  type ClientSnapshot,
  type ConnectionFailure,
  type Health,
  type HostSnapshot,
} from '@/lib/native/connection';

function useAlive(): { current: boolean } {
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  return alive;
}

/** Coalesced single-flight runner: a call while one is running schedules one trailing run. */
function useCoalesced(run: () => Promise<void>): () => Promise<void> {
  const running = useRef<Promise<void> | null>(null);
  const trailing = useRef(false);
  const runRef = useRef(run);
  runRef.current = run;
  return useCallback(() => {
    if (running.current) {
      trailing.current = true;
      return running.current;
    }
    const loop = async (): Promise<void> => {
      try {
        do {
          trailing.current = false;
          await runRef.current();
        } while (trailing.current);
      } finally {
        running.current = null;
      }
    };
    running.current = loop();
    return running.current;
  }, []);
}

export interface ClientReads {
  snapshot: ClientSnapshot | null;
  /** The last `client.status` read failed (the previous snapshot, if any, is kept). */
  readFailure: ConnectionFailure | null;
  refresh: () => Promise<void>;
}

/**
 * `client.status` on open and once per `app.stateChanged` (native emits one on
 * every client change), plus one `client.savedComputers` discovery on open.
 */
export function useClientSnapshot(app: AppState | null): ClientReads {
  const alive = useAlive();
  const revision = useRef(0);
  const [snapshot, setSnapshot] = useState<ClientSnapshot | null>(null);
  const [readFailure, setReadFailure] = useState<ConnectionFailure | null>(null);

  const refresh = useCoalesced(async () => {
    const token = ++revision.current;
    try {
      const next = parseClientSnapshot(await callConnection('client.status'));
      if (!alive.current || token !== revision.current) return;
      if (next) {
        setSnapshot(next);
        setReadFailure(null);
      } else setReadFailure(UNKNOWN_FAILURE);
    } catch (error) {
      if (alive.current && token === revision.current) setReadFailure(failureOf(error));
    }
  });

  useEffect(() => {
    // Discovery refreshes native's saved list; its failure (e.g. busy during an
    // attempt) is not shown: client.status carries the list it can read.
    void callConnection('client.savedComputers')
      .catch(() => undefined)
      .finally(() => {
        if (alive.current) void refresh();
      });
  }, [alive, refresh]);

  // One read per native status change (a new AppState object per apply).
  useEffect(() => {
    if (app) void refresh();
  }, [app, refresh]);

  return { snapshot, readFailure, refresh };
}

export type HostReads =
  | { kind: 'loading' }
  /** Host role absent (Mac, or a shell without host commands): hide the section. */
  | { kind: 'unsupported' }
  | { kind: 'failed'; failure: ConnectionFailure; snapshot: HostSnapshot | null }
  | { kind: 'ready'; snapshot: HostSnapshot };

export interface HostReadApi {
  state: HostReads;
  refresh: () => Promise<HostSnapshot | null>;
  /** Apply a snapshot returned by an action (`host.nextDevice`) if it is still the newest. */
  observe: (snapshot: HostSnapshot, token: number) => void;
  /** Current observation revision (taken before an action). */
  revision: () => number;
  /** Count of SUCCESSFUL observations (host.status read, event or action snapshot); failed reads never move it. */
  observed: number;
  /** The same count, read synchronously (taken right after an uncertain action returns). */
  observedCount: () => number;
  /** Retire reads and queued observations that began before terminal knowledge. */
  invalidate: () => number;
}

/** `host.status` on open + `host.stateChanged`; `unsupportedCommand` hides Host management. */
export function useHostSnapshot(): HostReadApi {
  const alive = useAlive();
  const revision = useRef(0);
  const last = useRef<HostSnapshot | null>(null);
  const seen = useRef(0);
  const eventFence = useRef(0);
  const [state, setState] = useState<HostReads>({ kind: 'loading' });
  const [observed, setObserved] = useState(0);

  const succeeded = useCallback(() => {
    seen.current += 1;
    setObserved(seen.current);
  }, []);

  const apply = useCallback(
    (snapshot: HostSnapshot) => {
      revision.current += 1;
      last.current = snapshot;
      if (snapshot.observation === 'known') succeeded();
      setState({ kind: 'ready', snapshot });
    },
    [succeeded],
  );

  const refresh = useCallback(async (): Promise<HostSnapshot | null> => {
    const token = ++revision.current;
    try {
      const next = parseHostSnapshot(await callConnection('host.status'));
      if (!alive.current || token !== revision.current) return null;
      if (!next) {
        setState({ kind: 'failed', failure: UNKNOWN_FAILURE, snapshot: last.current });
        return null;
      }
      last.current = next;
      if (next.observation === 'known') succeeded();
      setState({ kind: 'ready', snapshot: next });
      return next;
    } catch (error) {
      if (!alive.current || token !== revision.current) return null;
      const failure = failureOf(error);
      if (failure.code === 'unsupportedCommand') setState({ kind: 'unsupported' });
      else setState({ kind: 'failed', failure, snapshot: last.current });
      return null;
    }
  }, [alive, succeeded]);

  useEffect(() => {
    void refresh();
    const off = subscribeHostState((event) => {
      if (alive.current && Date.parse(event.observedAt) > eventFence.current) apply(event.snapshot);
    });
    return () => off?.();
  }, [alive, apply, refresh]);

  const observe = useCallback(
    (snapshot: HostSnapshot, token: number) => {
      if (alive.current && token === revision.current) apply(snapshot);
    },
    [alive, apply],
  );
  const current = useCallback(() => revision.current, []);
  const observedCount = useCallback(() => seen.current, []);
  const invalidate = useCallback(() => {
    revision.current += 1;
    eventFence.current = Date.now();
    return seen.current;
  }, []);
  return { state, refresh, observe, revision: current, observed, observedCount, invalidate };
}

export interface HealthReads {
  health: Health | null;
  failure: ConnectionFailure | null;
  checking: boolean;
  check: () => void;
}

/** `client.health` once while the active remote is connected, and on "Check connection". */
export function useHealth(active: boolean): HealthReads {
  const alive = useAlive();
  const [health, setHealth] = useState<Health | null>(null);
  const [failure, setFailure] = useState<ConnectionFailure | null>(null);
  const [checking, setChecking] = useState(false);
  const serial = useRef(0);

  const check = useCallback(() => {
    const token = ++serial.current;
    setChecking(true);
    callConnection('client.health')
      .then((raw) => {
        if (!alive.current || token !== serial.current) return;
        const parsed = parseHealth(raw);
        setHealth(parsed);
        setFailure(parsed ? null : UNKNOWN_FAILURE);
      })
      .catch((error: unknown) => {
        if (alive.current && token === serial.current) setFailure(failureOf(error));
      })
      .finally(() => {
        if (alive.current && token === serial.current) setChecking(false);
      });
  }, [alive]);

  useEffect(() => {
    if (active) check();
    else {
      serial.current += 1;
      setHealth(null);
      setFailure(null);
      setChecking(false);
    }
  }, [active, check]);

  return { health, failure, checking, check };
}
