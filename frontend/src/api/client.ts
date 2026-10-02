// Accès HTTP à l'API (lecture seule). Aucun composant n'appelle fetch() directement.
import type {
  EmailStatus,
  Health,
  HistoryFilters,
  NotificationRecord,
  QvdMeasure,
  ReloadEvent,
  ReloadPage,
  ReloadState,
} from "../types";

/** Base des URL : vide = même origine (proxy Vite en dev, FastAPI en production). */
export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null, // null = réseau / API injoignable
  ) {
    super(message);
    this.name = "ApiError";
  }
  get unreachable(): boolean {
    return this.status === null || this.status >= 502;
  }
}

type Query = Record<string, string | number | undefined | null>;

export function buildUrl(path: string, query: Query = {}): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

async function request<T>(path: string, query?: Query, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(buildUrl(path, query), { signal, headers: { Accept: "application/json" } });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("API injoignable", null);
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      /* corps non JSON : on garde le code HTTP */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export const getHealth = (signal?: AbortSignal) => request<Health>("/health", undefined, signal);

export const getEmailStatus = (signal?: AbortSignal) =>
  request<EmailStatus>("/api/notifications/status", undefined, signal);

export const getCurrentReload = (signal?: AbortSignal) =>
  request<ReloadState | null>("/api/reloads/current", undefined, signal);

export const getActiveReloads = (signal?: AbortSignal) =>
  request<ReloadState[]>("/api/reloads/active", undefined, signal);

export const PAGE_SIZE = 20;

export function getReloads(filters: HistoryFilters, page: number, signal?: AbortSignal) {
  return request<ReloadPage>(
    "/api/reloads",
    {
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
      platform: filters.platform,
      app: filters.app.trim(),
      status: filters.status,
      date_from: filters.dateFrom,
      date_to: filters.dateTo,
    },
    signal,
  );
}

export const getReload = (reloadId: string, signal?: AbortSignal) =>
  request<ReloadState>(`/api/reloads/${enc(reloadId)}`, undefined, signal);

export const getReloadEvents = (reloadId: string, afterSeq = 0, signal?: AbortSignal) =>
  request<ReloadEvent[]>(
    `/api/reloads/${enc(reloadId)}/events`,
    { after_seq: afterSeq, limit: 10000 },
    signal,
  );

export const getQvdMeasures = (reloadId: string, signal?: AbortSignal) =>
  request<QvdMeasure[]>(`/api/reloads/${enc(reloadId)}/qvd-measures`, undefined, signal);

export const getNotificationStatus = (reloadId: string, signal?: AbortSignal) =>
  request<NotificationRecord[]>(`/api/reloads/${enc(reloadId)}/notifications`, undefined, signal);
