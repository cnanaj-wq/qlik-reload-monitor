// Historique : page de résumés + détail chargé uniquement à l'ouverture d'un reload.
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  getNotificationStatus,
  getQvdMeasures,
  getReload,
  getReloadEvents,
  getReloads,
} from "../api/client";
import type {
  HistoryFilters,
  NotificationRecord,
  QvdMeasure,
  ReloadEvent,
  ReloadPage,
  ReloadState,
} from "../types";

export const EMPTY_FILTERS: HistoryFilters = {
  platform: "",
  app: "",
  status: "",
  dateFrom: "",
  dateTo: "",
};

export interface HistoryResult {
  data: ReloadPage | null;
  loading: boolean;
  error: string | null;
}

/** Une page de l'historique. Les données précédentes restent affichées pendant un rechargement. */
export function useReloadHistory(filters: HistoryFilters, page: number, tick = 0): HistoryResult {
  const [data, setData] = useState<ReloadPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    getReloads(filters, page, ctrl.signal)
      .then((p) => {
        setData(p);
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err.message : "Erreur inattendue");
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
    return () => ctrl.abort();
  }, [filters, page, tick]);

  return { data, loading, error };
}

export interface ReloadDetailData {
  state: ReloadState;
  events: ReloadEvent[];
  measures: QvdMeasure[];
  notifications: NotificationRecord[];
}

// Cache des détails : un reload terminé ne change plus, inutile de le recharger.
const detailCache = new Map<string, ReloadDetailData>();

/** Notification email encore susceptible d'évoluer (envoi en cours). */
export function hasPendingNotification(d: ReloadDetailData): boolean {
  return d.notifications.some((n) => n.status === "PENDING");
}

/**
 * Détail définitif : reload terminé ET notification réglée. Un reload en ERROR
 * sans notification n'est pas figé : sa ligne de notification peut être créée
 * juste après la fin du reload.
 */
export function isFinalDetail(d: ReloadDetailData): boolean {
  if (d.state.is_running || hasPendingNotification(d)) return false;
  return !(d.state.status === "ERROR" && d.notifications.length === 0);
}
export function clearDetailCache(): void {
  detailCache.clear();
}

export interface DetailResult {
  data: ReloadDetailData | null;
  loading: boolean;
  error: string | null;
}

/** Charge le détail d'un reload au moment où il est ouvert (4 appels en parallèle). */
export function useReloadDetail(reloadId: string | null, tick = 0): DetailResult {
  const [state, setState] = useState<DetailResult>({ data: null, loading: false, error: null });
  const lastId = useRef<string | null>(null);

  useEffect(() => {
    if (!reloadId) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    const cached = detailCache.get(reloadId);
    if (cached && isFinalDetail(cached)) {
      setState({ data: cached, loading: false, error: null });
      return;
    }
    const keepPrevious = lastId.current === reloadId;
    lastId.current = reloadId;
    const ctrl = new AbortController();
    setState((s) => ({ data: keepPrevious ? s.data : (cached ?? null), loading: true, error: null }));
    Promise.all([
      getReload(reloadId, ctrl.signal),
      getReloadEvents(reloadId, 0, ctrl.signal),
      getQvdMeasures(reloadId, ctrl.signal),
      getNotificationStatus(reloadId, ctrl.signal),
    ])
      .then(([st, events, measures, notifications]) => {
        const data = { state: st, events, measures, notifications };
        detailCache.set(reloadId, data);
        setState({ data, loading: false, error: null });
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setState((s) => ({
          data: s.data,
          loading: false,
          error: err instanceof ApiError ? err.message : "Erreur inattendue",
        }));
      });
    return () => ctrl.abort();
  }, [reloadId, tick]);

  return state;
}
