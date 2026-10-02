// Données de test et faux EventSource (aucun réseau dans les tests).
import type { EventSourceLike } from "../api/stream";
import type {
  Health,
  NotificationRecord,
  ReloadEvent,
  ReloadPage,
  ReloadState,
  ReloadSummary,
} from "../types";

let seqCounter = 0;
export function resetSeq(n = 0) {
  seqCounter = n;
}

export function ev(partial: Partial<ReloadEvent> & Pick<ReloadEvent, "event_type">): ReloadEvent {
  seqCounter += 1;
  return {
    seq: seqCounter,
    reload_id: "R1",
    timestamp: `2026-10-01T18:50:${String(seqCounter % 60).padStart(2, "0")}.000000`,
    source: "demo",
    platform: "qlik_sense",
    app_id: "app-ventes",
    app_name: "VENTES",
    status: "RUNNING",
    section: null,
    table: null,
    rows: null,
    qvd: null,
    qvd_path: null,
    qvd_size_bytes: null,
    message: null,
    extra: {},
    ...partial,
  };
}

export function state(partial: Partial<ReloadState> = {}): ReloadState {
  return {
    reload_id: "R1",
    app_id: "app-ventes",
    app_name: "VENTES",
    source: "qlik_log",
    platform: "qlik_sense",
    status: "RUNNING",
    is_running: true,
    started_at: "2026-10-01T18:50:02.000000",
    ended_at: null,
    elapsed_ms: 64000,
    current_step: "TABLE_PROGRESS VENTES",
    current_section: "FAITS",
    current_table: "VENTES",
    current_rows: 742184,
    current_qvd: "VENTES.qvd",
    current_qvd_size_bytes: 16 * 1024 * 1024,
    current_qvd_is_stable: false,
    current_qvd_writing: true,
    total_rows: 50744,
    warnings_count: 0,
    errors_count: 0,
    last_message: null,
    last_seq: 10,
    ...partial,
  };
}

export function summary(partial: Partial<ReloadSummary> = {}): ReloadSummary {
  return {
    reload_id: "R1",
    app_id: "app-ventes",
    app_name: "VENTES",
    source: "qlik_log",
    platform: "qlik_sense",
    status: "SUCCESS",
    started_at: "2026-10-01T22:15:42.000000",
    ended_at: "2026-10-01T22:17:00.000000",
    duration_ms: 78000,
    total_rows: 1887542,
    warnings_count: 1,
    errors_count: 0,
    qvd_count: 3,
    qvd_bytes: 38 * 1024 * 1024,
    ...partial,
  };
}

export function page(items: ReloadSummary[], total = items.length, offset = 0): ReloadPage {
  return { items, total, limit: 20, offset };
}

export function health(partial: Partial<Health> = {}): Health {
  return {
    status: "ok",
    version: "0.5.0",
    mode: "demo",
    database: "db",
    schema_version: 3,
    last_seq: 10,
    time: "2026-10-01T18:51:00",
    notifications: { email_enabled: true, email_dry_run: true },
    ...partial,
  };
}

export function notification(partial: Partial<NotificationRecord> = {}): NotificationRecord {
  return {
    id: 1,
    reload_id: "R1",
    channel: "email",
    recipient: "${ALERT_EMAIL_RECIPIENT}",
    status: "SENT",
    attempts: 1,
    subject: "#Error Reload QlikSense | VENTES | 01/10/2026 22:47",
    created_at: "2026-10-01T22:47:22",
    sent_at: "2026-10-01T22:47:23",
    error_message: null,
    ...partial,
  };
}

/** Reload Qlik Sense en cours : sections, table, QVD stabilisé + QVD en écriture. */
export function runningEvents(): ReloadEvent[] {
  resetSeq(0);
  return [
    ev({ event_type: "RELOAD_START" }),
    ev({ event_type: "SECTION_START", section: "DIMENSIONS" }),
    ev({ event_type: "TABLE_START", section: "DIMENSIONS", table: "CLIENTS" }),
    ev({ event_type: "TABLE_END", section: "DIMENSIONS", table: "CLIENTS", rows: 42318, status: "SUCCESS" }),
    ev({ event_type: "QVD_WRITE_START", section: "DIMENSIONS", qvd: "CLIENTS.qvd", qvd_path: "D:/QVD/CLIENTS.qvd" }),
    ev({ event_type: "QVD_SIZE_CHANGE", source: "qvd_watcher", qvd: "CLIENTS.qvd", qvd_size_bytes: 3_617_587 }),
    ev({ event_type: "QVD_SIZE_CHANGE", source: "qvd_watcher", qvd: "CLIENTS.qvd", qvd_size_bytes: 4_823_449 }),
    ev({ event_type: "QVD_STABLE", source: "qvd_watcher", qvd: "CLIENTS.qvd", qvd_size_bytes: 4_823_449, status: "SUCCESS" }),
    ev({ event_type: "QVD_WRITE_START", section: "DIMENSIONS", qvd: "VENTES.qvd" }),
    ev({ event_type: "QVD_SIZE_CHANGE", source: "qvd_watcher", qvd: "VENTES.qvd", qvd_size_bytes: 12 * 1024 * 1024 }),
  ];
}

export class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((ev: MessageEvent<string>) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  static reset() {
    FakeEventSource.instances = [];
  }
  static last(): FakeEventSource {
    const s = FakeEventSource.instances.at(-1);
    if (!s) throw new Error("aucun EventSource ouvert");
    return s;
  }
  addEventListener(type: string, listener: (ev: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.onopen?.(new Event("open"));
  }
  emit(event: ReloadEvent | string) {
    const data = typeof event === "string" ? event : JSON.stringify(event);
    for (const l of this.listeners.get("reload_event") ?? []) {
      l(new MessageEvent("reload_event", { data }));
    }
  }
  fail() {
    this.onerror?.(new Event("error"));
  }
}

export const fakeFactory = (url: string) => new FakeEventSource(url);

