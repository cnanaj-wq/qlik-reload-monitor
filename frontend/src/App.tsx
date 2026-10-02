import { useCallback, useState } from "react";
import { AppShell, type ViewId } from "./components/AppShell";
import { LiveView } from "./components/LiveView";
import { ReloadHistory } from "./components/ReloadHistory";
import { ApiUnavailableBanner, StreamInterruptedBanner } from "./components/States";
import { useLiveReload, type LiveOptions } from "./hooks/useLiveReload";

export function App({ liveOptions }: { liveOptions?: LiveOptions }) {
  const live = useLiveReload(liveOptions);
  const [view, setView] = useState<ViewId>("live");
  const [openRequest, setOpenRequest] = useState<string | null>(null);

  const openInHistory = useCallback((id: string) => {
    setOpenRequest(id);
    setView("history");
  }, []);

  return (
    <AppShell
      view={view}
      onViewChange={setView}
      api={live.apiStatus}
      stream={live.streamStatus}
      mode={live.health?.mode ?? null}
      email={live.emailStatus}
      banner={
        live.apiStatus === "unavailable" ? (
          <ApiUnavailableBanner lastEventAt={live.lastEventAt} />
        ) : live.apiStatus === "ok" && live.streamStatus === "reconnecting" ? (
          <StreamInterruptedBanner lastEventAt={live.lastEventAt} />
        ) : null
      }
    >
      {view === "live" ? (
        <LiveView live={live} onOpenInHistory={openInHistory} />
      ) : (
        <ReloadHistory tick={live.reloadTick} openRequest={openRequest} />
      )}
    </AppShell>
  );
}
