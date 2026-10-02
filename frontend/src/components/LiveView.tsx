import type { LiveReload } from "../hooks/useLiveReload";
import { AlertsPanel } from "./Alerts";
import { LiveTimeline } from "./LiveTimeline";
import { QvdPanel } from "./QvdPanel";
import { KpiStrip, ReloadHeader } from "./ReloadHeader";
import { RecentReloads } from "./ReloadHistory";
import { EmptyState, LoadingState } from "./States";

interface Props {
  live: LiveReload;
  onOpenInHistory: (reloadId: string) => void;
}

export function LiveView({ live, onOpenInHistory }: Props) {
  const { reload, events, health, apiStatus } = live;

  if (!reload) {
    if (apiStatus === "loading") return <LoadingState label="Connexion à l'API…" />;
    if (apiStatus === "unavailable") {
      return (
        <div className="px-6 py-8">
          <EmptyState title="Aucune donnée reçue pour l'instant.">
            L'API n'est pas joignable. Vérifier que le serveur tourne : <code className="text-fg">python -m app.api</code>
          </EmptyState>
        </div>
      );
    }
    return (
      <div className="space-y-6 px-6 py-8">
        <EmptyState title="Aucun reload disponible.">
          {health?.mode === "demo" ? (
            <>
              <p>Lancer un reload simulé depuis le dossier backend :</p>
              <pre className="mx-auto mt-2 w-fit rounded-md bg-panel px-3 py-2 text-left text-fg ring-1 ring-line">
                python -m app.demo successful --speed 10
              </pre>
              <p className="mt-2">Il apparaîtra ici en direct.</p>
            </>
          ) : (
            <p>Le prochain reload apparaîtra ici en direct.</p>
          )}
        </EmptyState>
      </div>
    );
  }

  return (
    <div>
      <ReloadHeader reload={reload} activeReloads={live.activeReloads} onSelect={live.selectReload} />
      <KpiStrip reload={reload} />
      <div className="grid gap-5 px-6 py-5 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <section aria-label="Timeline" className="min-w-0 rounded-lg border border-line bg-panel">
          <h3 className="flex items-baseline justify-between border-b border-line px-4 py-2 text-[13px] font-semibold text-muted">
            <span>Timeline</span>
            <span className="num font-normal">{events.length} événements</span>
          </h3>
          <LiveTimeline events={events} className="h-[28rem] lg:h-[calc(100vh-27rem)] lg:min-h-[22rem]" />
        </section>
        <aside className="scroll-quiet min-w-0 space-y-4 lg:h-[calc(100vh-24.5rem)] lg:min-h-[24rem] lg:overflow-y-auto">
          <AlertsPanel events={events} />
          <QvdPanel events={events} />
        </aside>
      </div>
      <RecentReloads tick={live.reloadTick} onOpen={onOpenInHistory} />
    </div>
  );
}
