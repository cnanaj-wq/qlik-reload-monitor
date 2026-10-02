import { memo, useCallback, useEffect, useRef, useState } from "react";
import { PAGE_SIZE } from "../api/client";
import { EMPTY_FILTERS, useReloadHistory } from "../hooks/useReloadHistory";
import { fmtBytes, fmtDateTime, fmtDuration, fmtInt } from "../lib/format";
import { PLATFORM_META } from "../lib/status";
import type { HistoryFilters, ReloadSummary } from "../types";
import { PlatformBadge, StatusBadge } from "./Badges";
import { Pagination } from "./Pagination";
import { ReloadDetail } from "./ReloadDetail";
import { ReloadFilters } from "./ReloadFilters";
import { EmptyState, ErrorState, LoadingState } from "./States";

const COLS =
  "grid grid-cols-[1.25rem_10.5rem_minmax(0,1fr)_7.5rem_5.5rem_8rem_7rem] items-center gap-x-4";

interface RowProps {
  reload: ReloadSummary;
  open: boolean;
  onToggle: (id: string) => void;
  tick: number;
}

/** Niveau 1 : une occurrence de reload. Le détail n'est chargé qu'à l'ouverture. */
export const ReloadHistoryRow = memo(function ReloadHistoryRow({ reload: r, open, onToggle, tick }: RowProps) {
  const detailId = `detail-${r.reload_id}`;
  return (
    <li className={`border-b border-line-soft ${open ? "bg-panel" : ""}`} data-reload={r.reload_id}>
      <button
        className={`${COLS} w-full px-4 py-2.5 text-left hover:bg-raised/50`}
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => onToggle(r.reload_id)}
      >
        <span aria-hidden="true" className="text-muted">{open ? "▾" : "▸"}</span>
        <span className="num">{fmtDateTime(r.started_at)}</span>
        <span className="flex min-w-0 items-center gap-2">
          <PlatformBadge platform={r.platform} />
          <span className="sr-only">{PLATFORM_META[r.platform].label}</span>
          <span className="truncate font-medium">{r.app_name}</span>
        </span>
        <span><StatusBadge status={r.status} /></span>
        <span className="num text-right">{r.duration_ms !== null ? fmtDuration(r.duration_ms) : "en cours"}</span>
        <span className="num text-right" title={r.status === "RUNNING" ? "Total disponible en fin de reload" : undefined}>
          {r.status === "RUNNING" ? "—" : fmtInt(r.total_rows)}
        </span>
        <span className="num flex justify-end gap-3 text-[13px]">
          <span className={r.warnings_count ? "text-warn" : "text-muted"} title="Warnings">
            ▲ {r.warnings_count}
          </span>
          <span className={r.errors_count ? "text-err" : "text-muted"} title="Erreurs">
            ✕ {r.errors_count}
          </span>
        </span>
      </button>
      {open && (
        <div id={detailId}>
          <ReloadDetail reloadId={r.reload_id} tick={tick} />
        </div>
      )}
    </li>
  );
});

interface HistoryProps {
  tick: number;
  /** Reload à ouvrir (ex. clic depuis l'écran LIVE). */
  openRequest?: string | null;
}

export function ReloadHistory({ tick, openRequest = null }: HistoryProps) {
  const [filters, setFilters] = useState<HistoryFilters>(EMPTY_FILTERS);
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(openRequest);
  const { data, loading, error } = useReloadHistory(filters, page, tick);
  const autoOpened = useRef(openRequest !== null);

  useEffect(() => {
    if (openRequest) setOpenId(openRequest);
  }, [openRequest]);

  // Le reload en cours peut s'ouvrir automatiquement, une seule fois.
  useEffect(() => {
    if (autoOpened.current || !data) return;
    autoOpened.current = true;
    const running = data.items.find((r) => r.status === "RUNNING");
    if (running) setOpenId(running.reload_id);
  }, [data]);

  const toggle = useCallback((id: string) => setOpenId((cur) => (cur === id ? null : id)), []);
  const changeFilters = useCallback((f: HistoryFilters) => {
    setFilters(f);
    setPage(0);
    setOpenId(null);
  }, []);

  return (
    <section aria-label="Historique des reloads" className="space-y-4 px-6 py-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <ReloadFilters value={filters} onChange={changeFilters} />
        <button
          className="rounded-md px-3 py-1.5 text-[13px] ring-1 ring-line hover:bg-raised disabled:opacity-40"
          disabled={openId === null}
          onClick={() => setOpenId(null)}
        >
          Tout réduire
        </button>
      </div>

      {error && (
        <ErrorState title="Historique indisponible">
          {error}. {data ? "Les dernières données chargées restent affichées." : "Nouvelle tentative au prochain changement."}
        </ErrorState>
      )}

      {!data && loading && <LoadingState label="Chargement de l'historique…" />}

      {data && data.total === 0 && !loading && (
        <EmptyState title="Aucun reload ne correspond à ces critères." />
      )}

      {data && data.items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line">
          <div className="min-w-[860px]">
            <div className={`${COLS} border-b border-line bg-panel px-4 py-2 text-[12px] text-muted`} aria-hidden="true">
              <span />
              <span>Début</span>
              <span>Plateforme · application</span>
              <span>Statut</span>
              <span className="text-right">Durée</span>
              <span className="text-right">Lignes</span>
              <span className="text-right">Alertes</span>
            </div>
            <ul aria-busy={loading} className={loading ? "opacity-70" : undefined}>
              {data.items.map((r) => (
                <ReloadHistoryRow key={r.reload_id} reload={r} open={openId === r.reload_id} onToggle={toggle} tick={tick} />
              ))}
            </ul>
          </div>
        </div>
      )}

      {data && data.total > 0 && (
        <Pagination page={page} pageSize={PAGE_SIZE} total={data.total} onPage={(p) => { setPage(p); setOpenId(null); }} />
      )}
    </section>
  );
}

/** Début de l'historique sur l'écran LIVE (5 derniers reloads). */
export function RecentReloads({ tick, onOpen }: { tick: number; onOpen: (id: string) => void }) {
  const { data } = useReloadHistory(EMPTY_FILTERS, 0, tick);
  const items = data?.items.slice(0, 5) ?? [];
  if (items.length === 0) return null;
  return (
    <section aria-label="Derniers reloads" className="px-6 pb-6">
      <h3 className="mb-2 text-[13px] font-semibold text-muted">Derniers reloads</h3>
      <ul className="overflow-hidden rounded-lg border border-line">
        {items.map((r) => (
          <li key={r.reload_id} className="border-b border-line-soft last:border-b-0">
            <button
              onClick={() => onOpen(r.reload_id)}
              className="grid w-full grid-cols-[10.5rem_minmax(0,1fr)_7.5rem_5.5rem_8rem_6rem] items-center gap-x-4 px-4 py-2 text-left hover:bg-raised/50"
              title="Ouvrir dans l'historique"
            >
              <span className="num">{fmtDateTime(r.started_at)}</span>
              <span className="flex min-w-0 items-center gap-2">
                <PlatformBadge platform={r.platform} />
                <span className="truncate">{r.app_name}</span>
              </span>
              <span><StatusBadge status={r.status} /></span>
              <span className="num text-right">{r.duration_ms !== null ? fmtDuration(r.duration_ms) : "en cours"}</span>
              <span className="num text-right">{r.status === "RUNNING" ? "—" : `${fmtInt(r.total_rows)} lignes`}</span>
              <span className="num text-right text-muted">{r.qvd_count ? fmtBytes(r.qvd_bytes) : "—"}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
