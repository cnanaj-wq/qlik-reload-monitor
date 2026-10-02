import { useEffect, useState } from "react";
import { PLATFORM_OPTIONS, STATUS_OPTIONS } from "../lib/status";
import type { EventStatus, HistoryFilters, Platform } from "../types";

interface Props {
  value: HistoryFilters;
  onChange: (f: HistoryFilters) => void;
}

const field =
  "rounded-md border border-line bg-panel px-2.5 py-1.5 text-[13px] text-fg focus:border-run focus:outline-none";

export function ReloadFilters({ value, onChange }: Props) {
  // La recherche texte n'interroge l'API qu'après une courte pause de saisie.
  const [app, setApp] = useState(value.app);
  useEffect(() => setApp(value.app), [value.app]);
  useEffect(() => {
    if (app === value.app) return;
    const t = setTimeout(() => onChange({ ...value, app }), 300);
    return () => clearTimeout(t);
  }, [app, value, onChange]);

  const set = <K extends keyof HistoryFilters>(k: K, v: HistoryFilters[K]) => onChange({ ...value, [k]: v });
  const active = Boolean(value.platform || value.app || value.status || value.dateFrom || value.dateTo);

  return (
    <form role="search" aria-label="Filtres de l'historique" className="flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
      <label className="flex flex-col gap-1 text-[12px] text-muted">
        Plateforme
        <select className={field} value={value.platform} onChange={(e) => set("platform", e.target.value as Platform | "")}>
          {PLATFORM_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">
        Application / document
        <input className={`${field} w-56`} type="search" placeholder="ex. VENTES" value={app} onChange={(e) => setApp(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">
        Statut
        <select className={field} value={value.status} onChange={(e) => set("status", e.target.value as EventStatus | "")}>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">
        Date début
        <input className={field} type="date" value={value.dateFrom} max={value.dateTo || undefined} onChange={(e) => set("dateFrom", e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-muted">
        Date fin
        <input className={field} type="date" value={value.dateTo} min={value.dateFrom || undefined} onChange={(e) => set("dateTo", e.target.value)} />
      </label>
      {active && (
        <button
          type="button"
          className="rounded-md px-2.5 py-1.5 text-[13px] text-muted ring-1 ring-line hover:text-fg"
          onClick={() => onChange({ platform: "", app: "", status: "", dateFrom: "", dateTo: "" })}
        >
          Effacer les filtres
        </button>
      )}
    </form>
  );
}
