import { useMemo, useState } from "react";
import { fmtTime } from "../lib/format";
import { errorParts, LONG_MESSAGE } from "../lib/timeline";
import type { ReloadEvent } from "../types";

/** Erreur immédiatement identifiable : statut, table, code, description, heure. */
export function ErrorCard({ event }: { event: ReloadEvent }) {
  const [open, setOpen] = useState(false);
  const { code, text } = errorParts(event.message);
  const long = (event.message?.length ?? 0) > LONG_MESSAGE || text !== event.message;
  return (
    <article role="alert" className="rounded-lg border border-err/50 bg-err/10 px-4 py-3" data-testid="error-card">
      <header className="flex items-center justify-between gap-3">
        <span className="font-semibold text-err">✕ ERROR</span>
        <time className="num text-[12px] text-muted">{fmtTime(event.timestamp)}</time>
      </header>
      {(event.section || event.table) && (
        <p className="mt-1 font-semibold">{[event.section, event.table].filter(Boolean).join(" › ")}</p>
      )}
      {code && <p className="num mt-1 font-semibold">{code}</p>}
      <p className="text-fg">{text}</p>
      {long && (
        <>
          <button
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-1 text-[12px] text-run underline-offset-2 hover:underline"
          >
            {open ? "Masquer le détail" : "Afficher le détail"}
          </button>
          {open && (
            <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-ink/60 p-2 text-[12px] text-muted">
              {event.message}
            </pre>
          )}
        </>
      )}
    </article>
  );
}

export function WarningCard({ event }: { event: ReloadEvent }) {
  // « Variable vDateDebut non définie : valeur par défaut utilisée »
  //   -> titre « Variable vDateDebut non définie » + détail « Valeur par défaut utilisée »
  const [head = "Warning", ...tail] = (event.message ?? "Warning").split(/\s+:\s+|\n/);
  const first = head;
  const restText = tail.join(" ");
  const rest = restText ? [restText.charAt(0).toUpperCase() + restText.slice(1)] : [];
  return (
    <li className="flex gap-3 rounded-md border border-warn/35 bg-warn/8 px-3 py-2" data-testid="warning-card">
      <span aria-hidden="true" className="text-warn">▲</span>
      <div className="min-w-0 flex-1">
        <p className="text-fg">
          <span className="sr-only">Warning : </span>
          {first}
        </p>
        {rest.length > 0 && <p className="text-muted">{rest.join(" ")}</p>}
        {(event.section || event.table) && (
          <p className="text-[12px] text-muted">{[event.section, event.table].filter(Boolean).join(" › ")}</p>
        )}
      </div>
      <time className="num shrink-0 text-[12px] text-muted">{fmtTime(event.timestamp)}</time>
    </li>
  );
}

/** Erreurs puis warnings du reload. Rien n'est affiché s'il n'y en a pas. */
export function AlertsPanel({ events }: { events: ReloadEvent[] }) {
  const { errors, warnings } = useMemo(
    () => ({
      errors: events.filter((e) => e.event_type === "ERROR"),
      warnings: events.filter((e) => e.event_type === "WARNING"),
    }),
    [events],
  );
  if (errors.length === 0 && warnings.length === 0) return null;
  return (
    <section aria-label="Erreurs et warnings" className="space-y-2">
      {errors.map((e) => (
        <ErrorCard key={e.seq} event={e} />
      ))}
      {warnings.length > 0 && (
        <ul className="space-y-1.5">
          {warnings.map((e) => (
            <WarningCard key={e.seq} event={e} />
          ))}
        </ul>
      )}
    </section>
  );
}
