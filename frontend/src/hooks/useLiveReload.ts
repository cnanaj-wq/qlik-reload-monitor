// État de l'écran LIVE : instantané initial + flux SSE, sans jamais effacer
// les données affichées en cas de coupure.
//
// Séquence (sans perte ni doublon) :
//   1. GET /api/reloads/current        -> reload affiché et son last_seq
//   2. GET /api/reloads/{id}/events    -> chronologie complète
//   3. /api/stream?after_seq=last_seq  -> événements suivants (dédupliqués par seq)
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getActiveReloads,
  getCurrentReload,
  getHealth,
  getReload,
  getReloadEvents,
} from "../api/client";
import { openReloadStream, type EventSourceFactory, type ReloadStream } from "../api/stream";
import { mergeEvents } from "../lib/timeline";
import type { ApiStatus, Health, ReloadEvent, ReloadState, StreamStatus } from "../types";

export interface LiveOptions {
  createSource?: EventSourceFactory;
  backoffMs?: number[];
  /** Intervalle de nouvelle tentative quand l'API est injoignable. */
  retryMs?: number;
  /** Regroupement des événements rapides avant rendu. */
  flushMs?: number;
  /** Délai minimal entre deux relectures de l'état du reload. */
  refreshMs?: number;
}

export interface LiveReload {
  apiStatus: ApiStatus;
  streamStatus: StreamStatus;
  health: Health | null;
  reload: ReloadState | null;
  events: ReloadEvent[];
  activeReloads: ReloadState[];
  /** Horodatage (côté données) du dernier événement reçu. */
  lastEventAt: string | null;
  /** Incrémenté à chaque début / fin de reload : l'historique se rafraîchit. */
  reloadTick: number;
  selectReload: (reloadId: string) => void;
}

export function useLiveReload(options: LiveOptions = {}): LiveReload {
  const { createSource, backoffMs, retryMs = 5000, flushMs = 50, refreshMs = 500 } = options;

  const [apiStatus, setApiStatus] = useState<ApiStatus>("loading");
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [health, setHealth] = useState<Health | null>(null);
  const [reload, setReload] = useState<ReloadState | null>(null);
  const [events, setEvents] = useState<ReloadEvent[]>([]);
  const [activeReloads, setActiveReloads] = useState<ReloadState[]>([]);
  const [lastEventAt, setLastEventAt] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  // Références stables pour les callbacks asynchrones.
  const displayedId = useRef<string | null>(null);
  const reloadRef = useRef<ReloadState | null>(null);
  const stream = useRef<ReloadStream | null>(null);
  const queue = useRef<ReloadEvent[]>([]);
  const timers = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());
  const disposed = useRef(false);
  const refreshPending = useRef(false);
  const apiDown = useRef(false);

  const later = useCallback((fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      timers.current.delete(t);
      if (!disposed.current) fn();
    }, ms);
    timers.current.add(t);
  }, []);

  const showReload = useCallback((r: ReloadState | null) => {
    reloadRef.current = r;
    setReload(r);
  }, []);

  const markDown = useCallback(() => {
    apiDown.current = true;
    setApiStatus("unavailable");
  }, []);

  const markUp = useCallback(() => {
    apiDown.current = false;
    setApiStatus("ok");
  }, []);

  // ------------------------------------------------------------ relectures

  const refreshActive = useCallback(async () => {
    try {
      setActiveReloads(await getActiveReloads());
    } catch {
      /* non bloquant */
    }
  }, []);

  const refreshDisplayed = useCallback(() => {
    if (refreshPending.current) return;
    refreshPending.current = true;
    later(async () => {
      refreshPending.current = false;
      const id = displayedId.current;
      if (!id) return;
      try {
        const r = await getReload(id);
        if (displayedId.current === id) showReload(r);
        if (apiDown.current) markUp();
      } catch {
        /* l'état précédent reste affiché */
      }
    }, refreshMs);
  }, [later, markUp, refreshMs, showReload]);

  const loadReloadEvents = useCallback(async (id: string) => {
    const all = await getReloadEvents(id);
    if (displayedId.current === id) setEvents((cur) => mergeEvents(cur, all));
  }, []);

  // ---------------------------------------------------------- flux SSE

  const flush = useCallback(() => {
    const batch = queue.current;
    queue.current = [];
    if (batch.length === 0) return;

    let switchTo: string | null = null;
    let lifecycle = false;
    const mine: ReloadEvent[] = [];

    for (const e of batch) {
      if (e.event_type === "RELOAD_START" || e.event_type === "RELOAD_END") lifecycle = true;
      if (
        e.event_type === "RELOAD_START" &&
        e.reload_id !== displayedId.current &&
        !(reloadRef.current?.is_running ?? false)
      ) {
        // Le reload affiché est terminé : on suit le nouveau.
        switchTo = e.reload_id;
        displayedId.current = e.reload_id;
        mine.length = 0;
      }
      if (e.reload_id === displayedId.current) mine.push(e);
    }

    setLastEventAt(batch[batch.length - 1]!.timestamp);
    if (switchTo) {
      setEvents(mine);
      const id = switchTo;
      void loadReloadEvents(id).catch(() => undefined);
    } else if (mine.length) {
      setEvents((cur) => mergeEvents(cur, mine));
    }
    if (mine.length || switchTo) refreshDisplayed();
    if (lifecycle) {
      setReloadTick((n) => n + 1);
      void refreshActive();
    }
  }, [loadReloadEvents, refreshActive, refreshDisplayed]);

  const onStreamEvent = useCallback(
    (e: ReloadEvent) => {
      const first = queue.current.length === 0;
      queue.current.push(e);
      if (first) later(flush, flushMs);
    },
    [flush, flushMs, later],
  );

  // ------------------------------------------------ disponibilité de l'API

  const probe = useCallback(async () => {
    try {
      setHealth(await getHealth());
      if (apiDown.current) {
        markUp();
        refreshDisplayed();
        void refreshActive();
      }
    } catch {
      markDown();
      later(() => void probe(), retryMs);
    }
  }, [later, markDown, markUp, refreshActive, refreshDisplayed, retryMs]);

  const openStream = useCallback(
    (afterSeq: number) => {
      stream.current?.close();
      stream.current = openReloadStream({
        afterSeq,
        createSource,
        backoffMs,
        onEvent: onStreamEvent,
        onStatus: (s) => {
          setStreamStatus(s);
          if (s === "reconnecting" && !apiDown.current) void probe();
        },
      });
    },
    [backoffMs, createSource, onStreamEvent, probe],
  );

  // ----------------------------------------------------- chargement initial

  const bootstrap = useCallback(async () => {
    try {
      const h = await getHealth();
      setHealth(h);
      const current = await getCurrentReload();
      let evts: ReloadEvent[] = [];
      if (current) {
        evts = await getReloadEvents(current.reload_id);
      }
      if (disposed.current) return;
      displayedId.current = current?.reload_id ?? null;
      showReload(current);
      setEvents(evts);
      if (evts.length) setLastEventAt(evts[evts.length - 1]!.timestamp);
      markUp();
      void refreshActive();
      const lastSeq = Math.max(current?.last_seq ?? 0, evts.at(-1)?.seq ?? 0);
      openStream(current ? lastSeq : h.last_seq);
    } catch {
      if (disposed.current) return;
      markDown();
      later(() => void bootstrap(), retryMs);
    }
  }, [later, markDown, markUp, openStream, refreshActive, retryMs, showReload]);

  useEffect(() => {
    disposed.current = false;
    void bootstrap();
    return () => {
      disposed.current = true;
      stream.current?.close();
      stream.current = null;
      for (const t of timers.current) clearTimeout(t);
      timers.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectReload = useCallback(
    (id: string) => {
      if (id === displayedId.current) return;
      displayedId.current = id;
      setEvents([]);
      const known = activeReloads.find((r) => r.reload_id === id) ?? null;
      showReload(known);
      void loadReloadEvents(id).catch(() => undefined);
      refreshDisplayed();
    },
    [activeReloads, loadReloadEvents, refreshDisplayed, showReload],
  );

  return {
    apiStatus,
    streamStatus,
    health,
    reload,
    events,
    activeReloads,
    lastEventAt,
    reloadTick,
    selectReload,
  };
}
