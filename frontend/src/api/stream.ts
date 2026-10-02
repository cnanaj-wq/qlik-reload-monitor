// Flux SSE des événements, avec reprise maîtrisée.
//
// La reconnexion automatique d'EventSource est volontairement remplacée par la
// nôtre : à chaque coupure, la connexion est fermée puis rouverte avec
// `after_seq = dernier seq reçu` (même effet que l'en-tête Last-Event-ID, que
// le serveur accepte aussi). On garde ainsi la main sur le délai, sur
// l'indicateur « Reconnexion… » et sur l'URL, qui ne rejoue jamais un ancien
// point de départ.
import type { ReloadEvent, StreamStatus } from "../types";
import { buildUrl } from "./client";

/** Sous-ensemble d'EventSource utilisé (remplaçable dans les tests). */
export interface EventSourceLike {
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void): void;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface StreamOptions {
  afterSeq: number;
  onEvent: (event: ReloadEvent) => void;
  onStatus: (status: StreamStatus) => void;
  createSource?: EventSourceFactory;
  /** Délais successifs avant reconnexion (ms) ; le dernier est répété. */
  backoffMs?: number[];
}

export interface ReloadStream {
  close(): void;
  readonly lastSeq: number;
}

const DEFAULT_BACKOFF = [1000, 2000, 5000, 10000];

const defaultFactory: EventSourceFactory = (url) =>
  new EventSource(url) as unknown as EventSourceLike;

export function streamUrl(afterSeq: number): string {
  return buildUrl("/api/stream", { after_seq: afterSeq });
}

export function openReloadStream(opts: StreamOptions): ReloadStream {
  const create = opts.createSource ?? defaultFactory;
  const backoff = opts.backoffMs ?? DEFAULT_BACKOFF;
  let lastSeq = opts.afterSeq;
  let source: EventSourceLike | null = null;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const connect = () => {
    if (closed) return;
    opts.onStatus(attempt === 0 ? "connecting" : "reconnecting");
    const es = create(streamUrl(lastSeq));
    source = es;

    es.onopen = () => {
      attempt = 0;
      opts.onStatus("live");
    };

    es.addEventListener("reload_event", (msg) => {
      let event: ReloadEvent;
      try {
        event = JSON.parse(msg.data) as ReloadEvent;
      } catch {
        return; // message illisible : ignoré, le flux continue
      }
      if (typeof event.seq !== "number" || event.seq <= lastSeq) return; // doublon / déjà reçu
      lastSeq = event.seq;
      opts.onEvent(event);
    });

    es.onerror = () => {
      es.close(); // on coupe la reconnexion native : la nôtre repart du dernier seq
      if (closed || source !== es) return;
      source = null;
      opts.onStatus("reconnecting");
      const delay = backoff[Math.min(attempt, backoff.length - 1)] ?? 10000;
      attempt += 1;
      timer = setTimeout(connect, delay);
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (timer) clearTimeout(timer);
      source?.close();
      source = null;
    },
    get lastSeq() {
      return lastSeq;
    },
  };
}
