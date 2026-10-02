import { useMemo, useState } from "react";
import { useReloadDetail } from "../hooks/useReloadHistory";
import { fmtBytes, fmtDateTime, fmtDuration, fmtInt, fmtTime } from "../lib/format";
import { buildTimeline, groupBySection, type SectionGroup as Group } from "../lib/timeline";
import { AlertsPanel } from "./Alerts";
import { StatusBadge } from "./Badges";
import { TimelineEvent } from "./LiveTimeline";
import { NotificationStatus } from "./NotificationStatus";
import { QvdPanel } from "./QvdPanel";
import { ErrorState, LoadingState } from "./States";

interface SectionGroupProps {
  group: Group;
  open: boolean;
  onToggle: () => void;
}

/** Niveau 2 : une section dépliable ; niveau 3 : ses événements. */
export function SectionGroup({ group, open, onToggle }: SectionGroupProps) {
  const items = useMemo(() => buildTimeline(group.events), [group.events]);
  const bodyId = `section-${group.key}`;
  return (
    <li className="rounded-md border border-line-soft" data-section={group.name}>
      <button
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={bodyId}
        className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-raised/60"
      >
        <span aria-hidden="true" className="w-3 text-muted">{open ? "▾" : "▸"}</span>
        <span className={`font-semibold ${group.isOther ? "text-muted" : ""}`}>{group.name}</span>
        <StatusBadge status={group.status} />
        <span className="num ml-auto text-[12px] text-muted">
          {group.startedAt && fmtTime(group.startedAt)}
          {group.durationMs !== null && ` · ${fmtDuration(group.durationMs)}`}
          {` · ${group.events.length} événement${group.events.length > 1 ? "s" : ""}`}
        </span>
      </button>
      {open && (
        <ol id={bodyId} className="border-t border-line-soft py-1">
          {items.map((it) => (
            <TimelineEvent key={it.seq} item={it} />
          ))}
        </ol>
      )}
    </li>
  );
}

function SummaryItem({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <dt className="text-[12px] text-muted">{label}</dt>
      <dd className={`num font-semibold ${tone ?? ""}`}>{value}</dd>
    </div>
  );
}

/** Détail d'une occurrence de reload, chargé uniquement à l'ouverture. */
export function ReloadDetail({ reloadId, tick = 0 }: { reloadId: string; tick?: number }) {
  const { data, loading, error } = useReloadDetail(reloadId, tick);
  const grouped = useMemo(
    () => (data ? groupBySection(data.events, data.state.is_running) : null),
    [data],
  );
  // Par défaut : sections en erreur / warning dépliées, les autres repliées.
  const [openKeys, setOpenKeys] = useState<Set<string> | null>(null);
  const keys =
    openKeys ??
    new Set(grouped?.sections.filter((g) => g.status === "ERROR" || g.status === "WARNING").map((g) => g.key));

  if (error && !data) return <div className="px-4 py-3"><ErrorState title="Détail indisponible">{error}</ErrorState></div>;
  if (!data || !grouped) return <LoadingState label="Chargement du détail…" />;

  const st = data.state;
  const head = buildTimeline(grouped.head);
  const tail = buildTimeline(data.events).filter((i) => i.event.event_type === "RELOAD_END");
  const toggle = (k: string) => {
    const next = new Set(keys);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setOpenKeys(next);
  };
  const qvdStable = data.measures.filter((m) => m.is_stable);

  return (
    <div className="grid gap-5 border-t border-line bg-ink/30 px-4 py-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]" data-testid="reload-detail">
      <div className="min-w-0 space-y-4">
        <dl className="grid grid-cols-3 gap-x-6 gap-y-2 sm:grid-cols-6">
          <SummaryItem label="Début" value={fmtTime(st.started_at)} />
          <SummaryItem label="Fin" value={st.ended_at ? fmtTime(st.ended_at) : "en cours"} />
          <SummaryItem label="Durée" value={fmtDuration(st.elapsed_ms)} />
          <SummaryItem label="Total lignes" value={fmtInt(st.total_rows)} />
          <SummaryItem label="Warnings" value={String(st.warnings_count)} tone={st.warnings_count ? "text-warn" : undefined} />
          <SummaryItem label="Errors" value={String(st.errors_count)} tone={st.errors_count ? "text-err" : undefined} />
        </dl>
        <NotificationStatus records={data.notifications} />

        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h4 className="mr-auto text-[13px] font-semibold text-muted">
              Chronologie complète · {fmtDateTime(st.started_at)}
            </h4>
            <button
              className="rounded-md px-2.5 py-1 text-[12px] ring-1 ring-line hover:bg-raised"
              onClick={() => setOpenKeys(new Set(grouped.sections.map((g) => g.key)))}
            >
              Déplier toutes les sections
            </button>
            <button
              className="rounded-md px-2.5 py-1 text-[12px] ring-1 ring-line hover:bg-raised"
              onClick={() => setOpenKeys(new Set())}
            >
              Réduire toutes les sections
            </button>
          </div>
          {head.length > 0 && (
            <ol className="mb-1">{head.map((it) => <TimelineEvent key={it.seq} item={it} />)}</ol>
          )}
          <ul className="space-y-1.5">
            {grouped.sections.map((g) => (
              <SectionGroup key={g.key} group={g} open={keys.has(g.key)} onToggle={() => toggle(g.key)} />
            ))}
          </ul>
          {tail.length > 0 && (
            <ol className="mt-1">{tail.map((it) => <TimelineEvent key={it.seq} item={it} />)}</ol>
          )}
          {loading && <p className="mt-2 text-[12px] text-muted">Actualisation…</p>}
        </div>
      </div>
      <aside className="min-w-0 space-y-4">
        <AlertsPanel events={data.events} />
        <QvdPanel events={data.events} />
        {qvdStable.length > 0 && (
          <p className="num text-[12px] text-muted">
            Volume QVD stabilisé : {fmtBytes(qvdStable.reduce((s, m) => s + m.size_bytes, 0))}
          </p>
        )}
      </aside>
    </div>
  );
}
