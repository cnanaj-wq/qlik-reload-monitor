import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNow } from "../hooks/useNow";
import { hasPendingNotification, useReloadDetail, type ReloadDetailData } from "../hooks/useReloadHistory";
import { fmtBytes, fmtDateTime, fmtDuration, fmtInt, fmtTime } from "../lib/format";
import { buildTimeline, groupBySection, type SectionGroup as Group } from "../lib/timeline";
import { AlertsPanel } from "./Alerts";
import { StatusBadge } from "./Badges";
import { TimelineEvent } from "./LiveTimeline";
import { NotificationStatus } from "./NotificationStatus";
import { QvdPanel } from "./QvdPanel";
import { ErrorState, LoadingState } from "./States";
import { elapsedMs } from "./ReloadHeader";

/** Intervalle d'actualisation d'un détail encore susceptible d'évoluer. */
export const DETAIL_POLL_MS = 3000;
/** Relectures maximales d'un reload en ERROR sans notification (création différée). */
const MAX_NOTIFICATION_CHECKS = 3;

/** Texte affiché quand un reload n'a (pas encore) de notification email. */
export function notificationHint(d: ReloadDetailData): string | null {
  if (d.state.status !== "ERROR") return null;
  if (d.state.is_running) return "Une notification sera émise à la fin du reload si son statut final reste ERROR.";
  return "Aucune notification enregistrée pour ce reload (email désactivé, ou reload terminé avant l'activation des notifications).";
}

/**
 * Actualise périodiquement le détail tant qu'il peut encore changer : reload en
 * cours, notification en cours d'envoi, ou notification pas encore créée.
 */
function useDetailPolling(data: ReloadDetailData | null, pollMs: number, refresh: () => void): void {
  const checks = useRef(0);
  const running = data?.state.is_running ?? false;
  const pending = data ? hasPendingNotification(data) : false;
  const awaitingNotification =
    !!data && !running && data.state.status === "ERROR" && data.notifications.length === 0;
  const active = running || pending || (awaitingNotification && checks.current < MAX_NOTIFICATION_CHECKS);
  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => {
      if (awaitingNotification) checks.current += 1;
      refresh();
    }, pollMs);
    return () => clearTimeout(t);
    // `data` : un nouveau délai après chaque relecture.
  }, [active, awaitingNotification, data, pollMs, refresh]);
}

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

function SummaryItem({ label, value, tone, hint }: { label: string; value: string; tone?: string; hint?: string }) {
  return (
    <div title={hint}>
      <dt className="text-[12px] text-muted">{label}</dt>
      <dd className={`num font-semibold ${tone ?? ""}`}>
        {value}
        {hint && <span className="ml-1 text-[11px] font-normal text-muted">(provisoire)</span>}
      </dd>
    </div>
  );
}

/** Détail d'une occurrence de reload, chargé uniquement à l'ouverture. */
export function ReloadDetail({
  reloadId,
  tick = 0,
  pollMs = DETAIL_POLL_MS,
}: {
  reloadId: string;
  tick?: number;
  pollMs?: number;
}) {
  const [poll, setPoll] = useState(0);
  const { data, loading, error } = useReloadDetail(reloadId, tick + poll);
  const refresh = useCallback(() => setPoll((n) => n + 1), []);
  useDetailPolling(data, pollMs, refresh);
  const now = useNow(data?.state.is_running ?? false);
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
          {st.is_running ? (
            <>
              <SummaryItem label="Durée écoulée" value={fmtDuration(elapsedMs(st, now))} />
              <SummaryItem
                label="Lignes à ce stade"
                value={fmtInt(st.total_rows)}
                hint="Valeur provisoire : le total définitif est connu à la fin du reload."
              />
            </>
          ) : (
            <>
              <SummaryItem label="Durée" value={fmtDuration(st.elapsed_ms)} />
              <SummaryItem label="Total lignes" value={fmtInt(st.total_rows)} />
            </>
          )}
          <SummaryItem label="Warnings" value={String(st.warnings_count)} tone={st.warnings_count ? "text-warn" : undefined} />
          <SummaryItem label="Errors" value={String(st.errors_count)} tone={st.errors_count ? "text-err" : undefined} />
        </dl>
        <NotificationStatus records={data.notifications} emptyHint={notificationHint(data)} />

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
