import type { ReactNode } from "react";
import { fmtTime } from "../lib/format";

export function LoadingState({ label = "Chargement…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 px-4 py-6 text-muted">
      <span aria-hidden="true" className="inline-block size-2 rounded-full bg-run/70" />
      {label}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center">
      <p className="text-[15px] text-fg">{title}</p>
      {children && <div className="mt-3 text-muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-err/40 bg-err/8 px-4 py-3">
      <p className="font-semibold text-err">{title}</p>
      {children && <div className="mt-1 text-muted">{children}</div>}
    </div>
  );
}

/**
 * Bandeau « flux temps réel interrompu » : l'API répond, seul le flux SSE est coupé.
 * Les données affichées sont conservées ; la reprise repart du dernier événement reçu.
 */
export function StreamInterruptedBanner({ lastEventAt }: { lastEventAt: string | null }) {
  return (
    <div
      role="status"
      data-testid="stream-banner"
      className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-warn/40 bg-warn/10 px-6 py-2"
    >
      <span className="font-semibold text-warn">○ Flux temps réel interrompu</span>
      <span className="text-muted">
        API joignable · données affichées conservées · dernier événement reçu :{" "}
        <span className="num text-fg">{lastEventAt ? fmtTime(lastEventAt) : "aucun"}</span>
      </span>
      <span className="text-muted">Reprise automatique sans perte depuis cet événement…</span>
    </div>
  );
}

/** Bandeau « API indisponible » : les dernières données restent affichées dessous. */
export function ApiUnavailableBanner({ lastEventAt }: { lastEventAt: string | null }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-6 gap-y-1 border-b border-err/40 bg-err/10 px-6 py-2.5">
      <span className="font-semibold text-err">✕ API indisponible</span>
      <span className="text-muted">
        Dernières données reçues : <span className="num text-fg">{lastEventAt ? fmtTime(lastEventAt) : "aucune"}</span>
      </span>
      <span className="text-muted">Tentative de reconnexion…</span>
    </div>
  );
}
