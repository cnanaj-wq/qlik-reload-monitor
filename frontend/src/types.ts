// Types alignés sur l'API FastAPI. Le frontend ne connaît que ces événements
// normalisés : il ignore tout du format des logs Qlik Sense ou QlikView.

export type Platform = "demo" | "qlik_sense" | "qlik_view";

export type EventStatus = "PENDING" | "RUNNING" | "SUCCESS" | "WARNING" | "ERROR";

export type EventSource = "demo" | "qlik_log" | "qvd_watcher";

export type EventType =
  | "RELOAD_START"
  | "RELOAD_END"
  | "SECTION_START"
  | "SECTION_END"
  | "TABLE_START"
  | "TABLE_PROGRESS"
  | "TABLE_END"
  | "QVD_WRITE_START"
  | "QVD_SIZE_CHANGE"
  | "QVD_WRITE_END"
  | "QVD_STABLE"
  | "WARNING"
  | "ERROR";

/** Horodatage ISO sans fuseau : heure locale de la machine qui exécute le monitor. */
export type LocalTimestamp = string;

export interface ReloadEvent {
  seq: number;
  reload_id: string;
  timestamp: LocalTimestamp;
  source: EventSource;
  platform: Platform;
  app_id: string;
  app_name: string;
  event_type: EventType;
  status: EventStatus;
  section: string | null;
  table: string | null;
  rows: number | null;
  qvd: string | null;
  qvd_path: string | null;
  qvd_size_bytes: number | null;
  message: string | null;
  extra: Record<string, unknown>;
}

export interface ReloadState {
  reload_id: string;
  app_id: string;
  app_name: string;
  source: EventSource;
  platform: Platform;
  status: EventStatus;
  is_running: boolean;
  started_at: LocalTimestamp;
  ended_at: LocalTimestamp | null;
  elapsed_ms: number;
  current_step: string | null;
  current_section: string | null;
  current_table: string | null;
  current_rows: number | null;
  current_qvd: string | null;
  current_qvd_size_bytes: number | null;
  current_qvd_is_stable: boolean | null;
  current_qvd_writing: boolean;
  total_rows: number;
  warnings_count: number;
  errors_count: number;
  last_message: string | null;
  last_seq: number | null;
}

export interface ReloadSummary {
  reload_id: string;
  app_id: string;
  app_name: string;
  source: EventSource;
  platform: Platform;
  status: EventStatus;
  started_at: LocalTimestamp;
  ended_at: LocalTimestamp | null;
  duration_ms: number | null;
  total_rows: number;
  warnings_count: number;
  errors_count: number;
  qvd_count: number;
  qvd_bytes: number;
}

export interface ReloadPage {
  items: ReloadSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface QvdMeasure {
  id: number;
  timestamp: LocalTimestamp;
  reload_id: string | null;
  qvd_name: string;
  path: string;
  size_bytes: number;
  delta_bytes: number;
  is_stable: boolean;
  source: EventSource;
}

export type NotificationStatus = "PENDING" | "SENT" | "FAILED" | "DRY_RUN";

export interface NotificationRecord {
  id: number;
  reload_id: string;
  channel: "email" | "teams" | "slack" | "webhook";
  recipient: string;
  status: NotificationStatus;
  attempts: number;
  subject: string | null;
  created_at: LocalTimestamp;
  sent_at: LocalTimestamp | null;
  error_message: string | null;
}

export interface Health {
  status: "ok";
  version: string;
  mode: "demo" | "live";
  database: string;
  schema_version: number;
  last_seq: number;
  time: LocalTimestamp;
  notifications: { email_enabled: boolean; email_dry_run: boolean };
}

export interface HistoryFilters {
  platform: Platform | "";
  app: string;
  status: EventStatus | "";
  dateFrom: string; // AAAA-MM-JJ
  dateTo: string;
}

/** État du flux temps réel affiché dans l'en-tête. */
export type StreamStatus = "connecting" | "live" | "reconnecting";

/** Disponibilité de l'API (indépendante du flux). */
export type ApiStatus = "loading" | "ok" | "unavailable";
