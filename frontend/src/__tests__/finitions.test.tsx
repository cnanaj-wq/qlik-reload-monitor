// Finitions de l'étape 5 : reloads RUNNING, totaux provisoires, coupure API / SSE,
// statuts de notification et indicateur email.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { App } from "../App";
import { EmailStatusIndicator } from "../components/AppShell";
import { NotificationStatus } from "../components/NotificationStatus";
import { ReloadDetail } from "../components/ReloadDetail";
import { RecentReloads, ReloadHistory } from "../components/ReloadHistory";
import { clearDetailCache, isFinalDetail } from "../hooks/useReloadHistory";
import {
  ev,
  fakeFactory,
  FakeEventSource,
  health,
  notification,
  page,
  resetSeq,
  runningEvents,
  state,
  summary,
} from "../test/fixtures";

vi.mock("../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../api/client")>();
  return {
    ...mod,
    getHealth: vi.fn(),
    getEmailStatus: vi.fn(),
    getCurrentReload: vi.fn(),
    getActiveReloads: vi.fn(),
    getReload: vi.fn(),
    getReloadEvents: vi.fn(),
    getReloads: vi.fn(),
    getQvdMeasures: vi.fn(),
    getNotificationStatus: vi.fn(),
  };
});
const api = vi.mocked(client);
const OPTIONS = { createSource: fakeFactory, backoffMs: [20], retryMs: 30, flushMs: 0, refreshMs: 0 };

const FINISHED_ERROR = { status: "ERROR" as const, is_running: false, ended_at: "2026-10-01T18:51:14" };

function mockLive() {
  api.getHealth.mockResolvedValue(health());
  api.getEmailStatus.mockResolvedValue({ enabled: true, dry_run: true, readiness: "dry_run", problems: [] });
  api.getCurrentReload.mockResolvedValue(state());
  api.getReload.mockResolvedValue(state());
  api.getReloadEvents.mockResolvedValue(runningEvents());
  api.getActiveReloads.mockResolvedValue([state()]);
  api.getReloads.mockResolvedValue(page([]));
}

function mockDetail(st = state(), notifications = [notification()]) {
  api.getReload.mockResolvedValue(st);
  api.getReloadEvents.mockResolvedValue([ev({ event_type: "RELOAD_START", reload_id: st.reload_id })]);
  api.getQvdMeasures.mockResolvedValue([]);
  api.getNotificationStatus.mockResolvedValue(notifications);
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.reset();
  resetSeq(0);
  clearDetailCache();
});

// ------------------------------------------------------------ reloads RUNNING

describe("reload RUNNING dans l'historique", () => {
  it("durée « en cours », lignes non affichées comme total définitif", async () => {
    api.getReloads.mockResolvedValue(page([summary({ reload_id: "R1", status: "RUNNING", duration_ms: null })]));
    mockDetail(state(), []);
    render(<ReloadHistory tick={0} />);
    const row = await screen.findByRole("button", { expanded: true }); // ouvert automatiquement
    expect(row).toHaveTextContent("en cours");
    expect(row).toHaveTextContent("—");
  });

  it("détail : « Lignes à ce stade (provisoire) » et pas de « Total lignes »", async () => {
    mockDetail(state({ total_rows: 50744 }), []);
    render(<ReloadDetail reloadId="R1" pollMs={10_000} />);
    const detail = await screen.findByTestId("reload-detail");
    expect(within(detail).getByText("Lignes à ce stade")).toBeInTheDocument();
    expect(within(detail).getByText("(provisoire)")).toBeInTheDocument();
    expect(within(detail).getByText("Durée écoulée")).toBeInTheDocument();
    expect(within(detail).queryByText("Total lignes")).not.toBeInTheDocument();
  });

  it("détail terminé : totaux définitifs, sans mention provisoire", async () => {
    mockDetail(state({ status: "SUCCESS", is_running: false, ended_at: "2026-10-01T18:51:14" }), []);
    render(<ReloadDetail reloadId="R1" pollMs={10_000} />);
    const detail = await screen.findByTestId("reload-detail");
    expect(within(detail).getByText("Total lignes")).toBeInTheDocument();
    expect(within(detail).queryByText("(provisoire)")).not.toBeInTheDocument();
  });

  it("détail en cours : actualisé périodiquement, puis figé une fois terminé", async () => {
    mockDetail(state(), []);
    render(<ReloadDetail reloadId="R1" pollMs={20} />);
    await screen.findByTestId("reload-detail");
    api.getReload.mockResolvedValue(state({ status: "SUCCESS", is_running: false, ended_at: "2026-10-01T18:51:14" }));
    await waitFor(() => expect(screen.getByText("Total lignes")).toBeInTheDocument());
    const calls = api.getReload.mock.calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect(api.getReload.mock.calls.length).toBe(calls);
  });

  it("derniers reloads : pas de volume QVD partiel pour un reload en cours", async () => {
    api.getReloads.mockResolvedValue(
      page([summary({ reload_id: "R1", status: "RUNNING", duration_ms: null, qvd_count: 1, qvd_bytes: 5_000_000 })]),
    );
    render(<RecentReloads tick={0} onOpen={() => undefined} />);
    const item = await screen.findByTitle("Ouvrir dans l'historique");
    expect(item).not.toHaveTextContent("MB");
    expect(item).toHaveTextContent("en cours");
  });
});

// ------------------------------------------------------- notifications

describe("statuts de notification", () => {
  it("reload en ERROR sans notification : explication affichée", () => {
    render(<NotificationStatus records={[]} emptyHint="Aucune notification enregistrée pour ce reload." />);
    expect(screen.getByTestId("notification-empty")).toHaveTextContent("Aucune notification enregistrée");
  });

  it("échec : conséquence explicite", () => {
    render(<NotificationStatus records={[notification({ status: "FAILED", attempts: 3, error_message: "authentification SMTP refusée (535)" })]} />);
    expect(screen.getByTestId("notification")).toHaveTextContent("L'erreur du reload n'a pas été notifiée par email.");
  });

  it("dry-run : aucun email n'est parti", () => {
    render(<NotificationStatus records={[notification({ status: "DRY_RUN", sent_at: null })]} />);
    expect(screen.getByTestId("notification")).toHaveTextContent("aucun email n'est parti");
  });

  it("détail d'un reload ERROR terminé sans notification : explication puis rafraîchissement", async () => {
    mockDetail(state(FINISHED_ERROR), []);
    render(<ReloadDetail reloadId="R1" pollMs={20} />);
    expect(await screen.findByTestId("notification-empty")).toBeInTheDocument();
    api.getNotificationStatus.mockResolvedValue([notification({ status: "SENT" })]);
    await waitFor(() => expect(screen.getByTestId("notification")).toHaveAttribute("data-status", "SENT"));
  });

  it("un détail n'est figé en cache que si sa notification est réglée", () => {
    const base = { events: [], measures: [] };
    const done = state(FINISHED_ERROR);
    expect(isFinalDetail({ ...base, state: done, notifications: [notification()] })).toBe(true);
    expect(isFinalDetail({ ...base, state: done, notifications: [] })).toBe(false);
    expect(isFinalDetail({ ...base, state: done, notifications: [notification({ status: "PENDING" })] })).toBe(false);
    expect(isFinalDetail({ ...base, state: state(), notifications: [notification()] })).toBe(false);
  });
});

describe("indicateur email", () => {
  it.each([
    ["disabled", "Email : désactivé"],
    ["dry_run", "Email : dry-run"],
    ["ready", "Email : actif"],
  ] as const)("%s", (readiness, text) => {
    render(<EmailStatusIndicator status={{ enabled: true, dry_run: false, readiness, problems: [] }} />);
    expect(screen.getByTestId("email-status")).toHaveTextContent(text);
  });

  it("configuration incomplète : problèmes en infobulle", () => {
    render(
      <EmailStatusIndicator
        status={{ enabled: true, dry_run: false, readiness: "incomplete", problems: ["serveur SMTP non défini"] }}
      />,
    );
    const el = screen.getByTestId("email-status");
    expect(el).toHaveTextContent("Email : configuration incomplète");
    expect(el).toHaveAttribute("title", expect.stringContaining("serveur SMTP non défini"));
  });

  it("affiché dans l'en-tête de l'application", async () => {
    mockLive();
    render(<App liveOptions={OPTIONS} />);
    expect(await screen.findByTestId("email-status")).toHaveTextContent("Email : dry-run");
  });
});

// ------------------------------------------------------ coupure API / SSE

describe("API indisponible ≠ flux SSE interrompu", () => {
  it("SSE coupé, API joignable : bandeau dédié, données conservées, puis disparition", async () => {
    mockLive();
    render(<App liveOptions={{ ...OPTIONS, backoffMs: [60_000] }} />);
    await screen.findByRole("region", { name: "Reload courant" });
    act(() => FakeEventSource.last().open());
    act(() => FakeEventSource.last().fail());
    const banner = await screen.findByTestId("stream-banner");
    expect(banner).toHaveTextContent("Flux temps réel interrompu");
    expect(banner).toHaveTextContent("API joignable");
    expect(screen.queryByText("✕ API indisponible")).not.toBeInTheDocument();
    expect(screen.getByText("Reload démarré")).toBeInTheDocument(); // rien n'est effacé
  });

  it("API injoignable : seul le bandeau API est affiché", async () => {
    mockLive();
    render(<App liveOptions={OPTIONS} />);
    await screen.findByRole("region", { name: "Reload courant" });
    act(() => FakeEventSource.last().open());
    api.getHealth.mockRejectedValue(new client.ApiError("API injoignable", null));
    act(() => FakeEventSource.last().fail());
    expect(await screen.findByRole("alert")).toHaveTextContent("API indisponible");
    expect(screen.queryByTestId("stream-banner")).not.toBeInTheDocument();
  });

  it("reprise SSE : repart du dernier seq reçu, sans doublon", async () => {
    mockLive();
    render(<App liveOptions={OPTIONS} />);
    await screen.findByRole("region", { name: "Reload courant" });
    const first = FakeEventSource.last();
    act(() => first.open());
    resetSeq(10);
    const e11 = ev({ event_type: "TABLE_PROGRESS", section: "FAITS", table: "VENTES", rows: 800_000 });
    act(() => first.emit(e11));
    act(() => first.fail());
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    const second = FakeEventSource.last();
    expect(second.url).toBe("/api/stream?after_seq=11");
    act(() => second.open());
    act(() => second.emit(e11)); // rejoué par erreur côté serveur : ignoré
    await waitFor(() => expect(screen.queryByTestId("stream-banner")).not.toBeInTheDocument());
    expect(screen.getAllByText(/800 000/).length).toBeGreaterThan(0);
  });
});
