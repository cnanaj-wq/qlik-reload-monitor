import type { ReactNode } from "react";
import type { ApiStatus, EmailStatus, StreamStatus } from "../types";

export type ViewId = "live" | "history";

// Les vues PERFORMANCE et QVD MONITOR viendront s'ajouter à cette liste.
export const VIEWS: { id: ViewId; label: string }[] = [
  { id: "live", label: "LIVE RELOAD" },
  { id: "history", label: "HISTORIQUE" },
];

export function NavigationTabs({ view, onChange }: { view: ViewId; onChange: (v: ViewId) => void }) {
  return (
    <nav role="tablist" aria-label="Vues" className="flex gap-1">
      {VIEWS.map((v) => {
        const active = v.id === view;
        return (
          <button
            key={v.id}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(v.id)}
            className={`rounded-md px-3 py-1.5 text-[13px] font-semibold tracking-wide transition-colors ${
              active ? "bg-raised text-fg ring-1 ring-line" : "text-muted hover:text-fg"
            }`}
          >
            {v.label}
          </button>
        );
      })}
    </nav>
  );
}

const EMAIL_META: Record<EmailStatus["readiness"], { text: string; cls: string; title: string }> = {
  disabled: { text: "Email : désactivé", cls: "text-muted", title: "Aucune alerte email ne sera envoyée." },
  dry_run: {
    text: "Email : dry-run",
    cls: "text-muted",
    title: "Les emails d'erreur sont construits et journalisés, mais rien n'est envoyé.",
  },
  ready: { text: "Email : actif", cls: "text-ok", title: "Un email est envoyé pour chaque reload terminé en erreur." },
  incomplete: {
    text: "Email : configuration incomplète",
    cls: "text-warn",
    title: "Envoi réel demandé mais impossible",
  },
};

/** Mode des alertes email, pour ne jamais croire à tort qu'une erreur sera notifiée. */
export function EmailStatusIndicator({ status }: { status: EmailStatus | null }) {
  if (!status) return null;
  const meta = EMAIL_META[status.readiness];
  const title = status.problems.length ? `${meta.title} : ${status.problems.join(" ; ")}` : meta.title;
  return (
    <span className={`text-[12px] ${meta.cls}`} title={title} data-testid="email-status" data-readiness={status.readiness}>
      {meta.text}
    </span>
  );
}

export function ConnectionStatus({ api, stream }: { api: ApiStatus; stream: StreamStatus }) {
  let text: string;
  let cls: string;
  if (api === "unavailable") {
    text = "○ API indisponible";
    cls = "text-err";
  } else if (stream === "live") {
    text = "● LIVE";
    cls = "text-ok";
  } else if (stream === "reconnecting") {
    text = "○ Reconnexion…";
    cls = "text-warn";
  } else {
    text = "○ Connexion…";
    cls = "text-muted";
  }
  return (
    <span role="status" aria-live="polite" className={`text-[13px] font-medium ${cls}`} data-testid="connection">
      {text}
    </span>
  );
}

interface AppShellProps {
  view: ViewId;
  onViewChange: (v: ViewId) => void;
  api: ApiStatus;
  stream: StreamStatus;
  mode: string | null;
  email?: EmailStatus | null;
  banner?: ReactNode;
  children: ReactNode;
}

export function AppShell({ view, onViewChange, api, stream, mode, email = null, banner, children }: AppShellProps) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 flex items-center gap-6 border-b border-line bg-ink/95 px-6 py-2.5 backdrop-blur">
        <h1 className="flex items-center gap-2 text-[15px] font-semibold tracking-wide">
          <span aria-hidden="true" className="grid size-5 place-items-center rounded bg-run/15 text-[11px] text-run">
            ◆
          </span>
          QLIK RELOAD MONITOR
        </h1>
        <NavigationTabs view={view} onChange={onViewChange} />
        <div className="ml-auto flex items-center gap-4">
          {mode === "demo" && (
            <span className="rounded px-1.5 py-px text-[11px] font-semibold text-muted ring-1 ring-line" title="Données simulées">
              MODE DEMO
            </span>
          )}
          <EmailStatusIndicator status={email} />
          <span className="text-[12px] text-muted">Lecture seule</span>
          <ConnectionStatus api={api} stream={stream} />
        </div>
      </header>
      {banner}
      <main className="flex-1">{children}</main>
    </div>
  );
}
