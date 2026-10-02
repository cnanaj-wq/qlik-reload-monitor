import { useNow } from "../hooks/useNow";
import { fmtBytes, fmtClock, fmtDateTime, fmtInt, parseLocal } from "../lib/format";
import { stepLabel } from "../lib/timeline";
import type { ReloadState } from "../types";
import { PlatformBadge, ReloadTitle, StatusBadge } from "./Badges";

export function elapsedMs(reload: ReloadState, now: Date): number {
  if (!reload.is_running) return reload.elapsed_ms;
  const start = parseLocal(reload.started_at).getTime();
  return Number.isNaN(start) ? reload.elapsed_ms : Math.max(0, now.getTime() - start);
}

interface ReloadHeaderProps {
  reload: ReloadState;
  activeReloads: ReloadState[];
  onSelect: (reloadId: string) => void;
}

export function ReloadHeader({ reload, activeReloads, onSelect }: ReloadHeaderProps) {
  const now = useNow(reload.is_running);
  const others = activeReloads.filter((r) => r.reload_id !== reload.reload_id);
  return (
    <section aria-label="Reload courant" className="flex flex-wrap items-end gap-x-10 gap-y-3 px-6 pb-4 pt-5">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <PlatformBadge platform={reload.platform} />
          <span className="text-[12px] text-muted" title="Identifiant du reload">
            {reload.reload_id}
          </span>
        </div>
        <h2 className="mt-1.5 truncate text-[26px] font-semibold leading-tight tracking-tight">
          <ReloadTitle platform={reload.platform} appName={reload.app_name} />
        </h2>
      </div>
      <StatusBadge status={reload.status} size="lg" live={reload.is_running} />
      <dl className="flex gap-8 text-[13px]">
        <div>
          <dt className="text-muted">Début</dt>
          <dd className="num text-[15px]">{fmtDateTime(reload.started_at)}</dd>
        </div>
        {reload.ended_at && (
          <div>
            <dt className="text-muted">Fin</dt>
            <dd className="num text-[15px]">{fmtDateTime(reload.ended_at)}</dd>
          </div>
        )}
        <div>
          <dt className="text-muted">Durée</dt>
          <dd className="num text-[15px]" data-testid="elapsed">
            {fmtClock(elapsedMs(reload, now))}
          </dd>
        </div>
      </dl>
      {others.length > 0 && (
        <div className="ml-auto flex items-center gap-2 text-[13px]">
          <span className="text-muted">
            {others.length === 1 ? "Autre reload en cours :" : `${others.length} autres reloads en cours :`}
          </span>
          {others.map((r) => (
            <button
              key={r.reload_id}
              onClick={() => onSelect(r.reload_id)}
              className="rounded-md px-2 py-1 ring-1 ring-line hover:bg-raised"
            >
              <PlatformBadge platform={r.platform} /> <span className="ml-1">{r.app_name}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------- KPI

interface KpiProps {
  label: string;
  value: string | null;
  tone?: "warn" | "err" | null;
  wide?: boolean;
}

export function KpiCard({ label, value, tone = null, wide = false }: KpiProps) {
  const color = tone === "err" ? "text-err" : tone === "warn" ? "text-warn" : "text-fg";
  return (
    <div className={`min-w-0 px-4 py-3 ${wide ? "col-span-2 sm:col-span-1" : ""}`}>
      <dt className="truncate text-[12px] text-muted">{label}</dt>
      <dd className={`num mt-0.5 truncate text-[17px] font-semibold ${color}`} title={value ?? undefined}>
        {value ?? <span className="font-normal text-muted">—</span>}
      </dd>
    </div>
  );
}

/** Une seule bande découpée par des filets (pas une grille de cartes). */
export function KpiStrip({ reload }: { reload: ReloadState }) {
  const w = reload.warnings_count;
  const e = reload.errors_count;
  return (
    <dl
      aria-label="Indicateurs du reload"
      className="mx-6 grid grid-cols-2 divide-line overflow-hidden rounded-lg border border-line bg-panel sm:grid-cols-3 lg:grid-cols-9 lg:divide-x [&>*]:border-line [&>*]:max-lg:border-b"
    >
      <KpiCard label="Étape actuelle" value={stepLabel(reload.current_step)} wide />
      <KpiCard label="Section" value={reload.current_section} />
      <KpiCard label="Table courante" value={reload.current_table} />
      <KpiCard label="Lignes courantes" value={reload.current_rows !== null ? fmtInt(reload.current_rows) : null} />
      <KpiCard label="Total chargé" value={fmtInt(reload.total_rows)} />
      {/* Pendant une écriture : « QVD courant ». Sinon, le dernier QVD connu, avec son état. */}
      <KpiCard
        label={reload.current_qvd_writing ? "QVD courant" : "Dernier QVD"}
        value={reload.current_qvd}
      />
      <KpiCard
        label={reload.current_qvd_writing ? "Taille actuelle" : "Taille"}
        value={
          reload.current_qvd_size_bytes !== null
            ? `${fmtBytes(reload.current_qvd_size_bytes)}${reload.current_qvd_is_stable ? " ✓ stable" : ""}`
            : null
        }
      />
      <KpiCard label="Warnings" value={w > 0 ? `▲ ${w}` : "0"} tone={w > 0 ? "warn" : null} />
      <KpiCard label="Errors" value={e > 0 ? `✕ ${e}` : "0"} tone={e > 0 ? "err" : null} />
    </dl>
  );
}
