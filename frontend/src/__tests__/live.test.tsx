import { act, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { App } from "../App";
import { PlatformBadge, StatusBadge } from "../components/Badges";
import type { EventStatus, Platform } from "../types";
import { ev, fakeFactory, FakeEventSource, health, resetSeq, runningEvents, state } from "../test/fixtures";

vi.mock("../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../api/client")>();
  return {
    ...mod,
    getHealth: vi.fn(),
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

function mockRunning(overrides = {}) {
  const events = runningEvents();
  api.getHealth.mockResolvedValue(health());
  api.getCurrentReload.mockResolvedValue(state({ last_seq: 10, ...overrides }));
  api.getReload.mockResolvedValue(state({ last_seq: 10, ...overrides }));
  api.getReloadEvents.mockResolvedValue(events);
  api.getActiveReloads.mockResolvedValue([state()]);
  api.getReloads.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  return events;
}

async function renderLive() {
  render(<App liveOptions={OPTIONS} />);
  await screen.findByRole("region", { name: "Reload courant" });
}

beforeEach(() => {
  FakeEventSource.reset();
  resetSeq(0);
});

describe("écran LIVE", () => {
  it("base vide : message et commande de démo", async () => {
    api.getHealth.mockResolvedValue(health({ last_seq: 0 }));
    api.getCurrentReload.mockResolvedValue(null);
    api.getActiveReloads.mockResolvedValue([]);
    render(<App liveOptions={OPTIONS} />);
    expect(await screen.findByText("Aucun reload disponible.")).toBeInTheDocument();
    expect(screen.getByText("python -m app.demo successful --speed 10")).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.last().url).toBe("/api/stream?after_seq=0"));
  });

  it("base vide hors DEMO : pas de commande de démo", async () => {
    api.getHealth.mockResolvedValue(health({ mode: "live" }));
    api.getCurrentReload.mockResolvedValue(null);
    api.getActiveReloads.mockResolvedValue([]);
    render(<App liveOptions={OPTIONS} />);
    await screen.findByText("Aucun reload disponible.");
    expect(screen.queryByText(/python -m app.demo/)).not.toBeInTheDocument();
  });

  it("reload RUNNING : en-tête, statut et KPI", async () => {
    mockRunning();
    await renderLive();
    const header = screen.getByRole("region", { name: "Reload courant" });
    expect(within(header).getByText("Qlik Sense ·", { exact: false })).toBeInTheDocument();
    expect(within(header).getByText("VENTES")).toBeInTheDocument();
    expect(within(header).getByText("RUNNING")).toBeInTheDocument();
    expect(within(header).getByText("01/10/2026 18:50:02")).toBeInTheDocument();
    const kpis = screen.getByLabelText("Indicateurs du reload");
    expect(within(kpis).getByText("Chargement VENTES")).toBeInTheDocument();
    expect(within(kpis).getByText("742 184")).toBeInTheDocument();
    expect(within(kpis).getByText("50 744")).toBeInTheDocument();
    expect(within(kpis).getByText("16.0 MB")).toBeInTheDocument();
    expect(within(kpis).getByText("QVD courant")).toBeInTheDocument();
  });

  it.each<[EventStatus, string]>([
    ["SUCCESS", "SUCCESS"],
    ["WARNING", "WARNING"],
    ["ERROR", "ERROR"],
  ])("reload terminé %s", async (status, label) => {
    mockRunning({ status, is_running: false, ended_at: "2026-10-01T18:51:45", current_qvd_writing: false });
    await renderLive();
    const header = screen.getByRole("region", { name: "Reload courant" });
    expect(within(header).getByText(label)).toBeInTheDocument();
    expect(within(header).getByText("Fin")).toBeInTheDocument();
    expect(within(header).getByTestId("elapsed")).toHaveTextContent("00:01:04");
  });

  it("timeline du reload", async () => {
    mockRunning();
    await renderLive();
    const timeline = screen.getByRole("list", { name: "Chronologie du reload" });
    expect(within(timeline).getByText("Reload démarré")).toBeInTheDocument();
    expect(within(timeline).getByText("CLIENTS.qvd stabilisé")).toBeInTheDocument();
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(10);
  });

  it("QVD en écriture et QVD stabilisé", async () => {
    mockRunning();
    await renderLive();
    const panel = screen.getByRole("region", { name: "QVD du reload" });
    const stable = within(panel).getByRole("article", { name: "CLIENTS.qvd STABLE" });
    expect(within(stable).getByText("4.6 MB")).toBeInTheDocument();
    expect(within(stable).getByText("+1.1 MB")).toBeInTheDocument();
    const writing = within(panel).getByRole("article", { name: "VENTES.qvd WRITING" });
    expect(within(writing).getByText("Taille actuelle")).toBeInTheDocument();
    expect(within(writing).getByText("12.0 MB")).toBeInTheDocument();
    expect(panel.textContent).not.toMatch(/%/); // jamais de pourcentage inventé
    expect(within(panel).getByText("1/2 stabilisés")).toBeInTheDocument();
  });

  it("SSE : nouveaux événements affichés, doublons ignorés", async () => {
    mockRunning();
    await renderLive();
    const src = FakeEventSource.last();
    expect(src.url).toBe("/api/stream?after_seq=10");
    act(() => src.open());
    expect(screen.getByTestId("connection")).toHaveTextContent("● LIVE");

    resetSeq(10);
    const warn = ev({ event_type: "WARNING", status: "WARNING", message: "Clé synthétique créée : $Syn 1" });
    const stable = ev({ event_type: "QVD_STABLE", qvd: "VENTES.qvd", qvd_size_bytes: 33_344_716, status: "SUCCESS" });
    act(() => {
      src.emit(warn);
      src.emit(warn);
      src.emit(stable);
    });
    const timeline = screen.getByRole("list", { name: "Chronologie du reload" });
    await waitFor(() => expect(within(timeline).getAllByRole("listitem")).toHaveLength(12));
    expect(within(timeline).getByText("VENTES.qvd stabilisé")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "VENTES.qvd STABLE" })).toBeInTheDocument();
    expect(screen.getAllByTestId("warning-card")).toHaveLength(1);
  });

  it("plusieurs événements rapides et un doublon déjà chargé", async () => {
    const events = mockRunning();
    await renderLive();
    const src = FakeEventSource.last();
    resetSeq(10);
    act(() => {
      src.emit(events[9]!); // déjà reçu au chargement (seq 10)
      for (let i = 0; i < 30; i++) {
        src.emit(ev({ event_type: "QVD_SIZE_CHANGE", qvd: "VENTES.qvd", qvd_size_bytes: (13 + i) * 1024 * 1024 }));
      }
    });
    const timeline = screen.getByRole("list", { name: "Chronologie du reload" });
    await waitFor(() => expect(within(timeline).getAllByRole("listitem")).toHaveLength(40));
    expect(within(screen.getByRole("article", { name: "VENTES.qvd WRITING" })).getByText("42.0 MB")).toBeInTheDocument();
  });

  it("warning et erreur visibles (compteurs, timeline, cartes)", async () => {
    mockRunning({ status: "ERROR", warnings_count: 1, errors_count: 1 });
    resetSeq(10);
    const extra = [
      ev({ event_type: "WARNING", status: "WARNING", section: "PARAMETRES", message: "Variable vDateDebut non définie : valeur par défaut utilisée" }),
      ev({ event_type: "ERROR", status: "ERROR", section: "FAITS", table: "VENTES", rows: 737022,
           message: "SQL##f - SqlState: 42S02, ErrorCode: 942, ErrorMsg: ORA-00942: table or view does not exist" }),
    ];
    api.getReloadEvents.mockResolvedValue([...runningEvents(), ...extra]);
    await renderLive();
    const kpis = screen.getByLabelText("Indicateurs du reload");
    expect(within(kpis).getByText("▲ 1")).toBeInTheDocument();
    expect(within(kpis).getByText("✕ 1")).toBeInTheDocument();
    const card = screen.getByTestId("error-card");
    expect(within(card).getByText("ORA-00942")).toBeInTheDocument();
    expect(within(card).getByText("table or view does not exist")).toBeInTheDocument();
    expect(within(card).getByText("FAITS › VENTES")).toBeInTheDocument();
    const warn = screen.getByTestId("warning-card");
    expect(within(warn).getByText("Variable vDateDebut non définie")).toBeInTheDocument();
    expect(within(warn).getByText("Valeur par défaut utilisée")).toBeInTheDocument();
    // Détail complet accessible pour les messages longs
    act(() => within(card).getByRole("button", { name: "Afficher le détail" }).click());
    expect(within(card).getByText(/SqlState: 42S02/)).toBeInTheDocument();
  });

  it("SSE coupé : « Reconnexion… », données conservées, reprise depuis le dernier seq", async () => {
    mockRunning();
    await renderLive();
    const first = FakeEventSource.last();
    act(() => first.open());
    resetSeq(10);
    act(() => first.emit(ev({ event_type: "TABLE_PROGRESS", section: "FAITS", table: "VENTES", rows: 800000 })));
    act(() => first.fail());
    expect(screen.getByTestId("connection")).toHaveTextContent("○ Reconnexion…");
    expect(screen.getByRole("list", { name: "Chronologie du reload" })).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(FakeEventSource.last().url).toBe("/api/stream?after_seq=11");
    act(() => FakeEventSource.last().open());
    expect(screen.getByTestId("connection")).toHaveTextContent("● LIVE");
  });

  it("API indisponible : bandeau, dernières données conservées, puis rétablissement", async () => {
    mockRunning();
    await renderLive();
    act(() => FakeEventSource.last().open());
    api.getHealth.mockRejectedValue(new client.ApiError("API injoignable", null));
    act(() => FakeEventSource.last().fail());
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("API indisponible");
    expect(banner).toHaveTextContent("Dernières données reçues : 18:50:10");
    expect(banner).toHaveTextContent("Tentative de reconnexion…");
    expect(screen.getByText("Reload démarré")).toBeInTheDocument(); // rien n'est effacé
    api.getHealth.mockResolvedValue(health());
    await waitFor(() => expect(screen.queryByText("Tentative de reconnexion…")).not.toBeInTheDocument());
  });

  it("API injoignable dès le démarrage : aucun écran blanc", async () => {
    api.getHealth.mockRejectedValue(new client.ApiError("API injoignable", null));
    render(<App liveOptions={OPTIONS} />);
    expect(await screen.findByText("Aucune donnée reçue pour l'instant.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("API indisponible");
  });

  it("suit le nouveau reload quand le précédent est terminé", async () => {
    mockRunning({ status: "SUCCESS", is_running: false, ended_at: "2026-10-01T18:51:45" });
    await renderLive();
    api.getReload.mockResolvedValue(state({ reload_id: "R2", app_name: "STOCKS", platform: "qlik_view", last_seq: 11 }));
    api.getReloadEvents.mockResolvedValue([]);
    resetSeq(10);
    act(() =>
      FakeEventSource.last().emit(ev({ event_type: "RELOAD_START", reload_id: "R2", app_name: "STOCKS", platform: "qlik_view" })),
    );
    const header = screen.getByRole("region", { name: "Reload courant" });
    await waitFor(() => expect(within(header).getByText("STOCKS")).toBeInTheDocument());
    expect(within(header).getByText("VIEW")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Chronologie du reload" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("interface en lecture seule : aucune action sur Qlik", async () => {
    mockRunning();
    await renderLive();
    for (const name of [/reload$/i, /stop/i, /restart/i, /redémarrer/i, /supprimer|delete/i, /modifier|modify/i, /arrêter/i]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});

describe("badges", () => {
  it.each<[Platform, string, string]>([
    ["qlik_sense", "SENSE", "Qlik Sense"],
    ["qlik_view", "VIEW", "QlikView"],
    ["demo", "DEMO", "DEMO"],
  ])("plateforme %s", (platform, badge, title) => {
    render(<PlatformBadge platform={platform} />);
    const el = screen.getByText(badge);
    expect(el).toHaveAttribute("title", title);
  });

  it.each<[EventStatus, string, string]>([
    ["RUNNING", "●", "RUNNING"],
    ["SUCCESS", "✓", "SUCCESS"],
    ["WARNING", "▲", "WARNING"],
    ["ERROR", "✕", "ERROR"],
    ["PENDING", "○", "WAITING"],
  ])("statut %s : symbole + texte, jamais la couleur seule", (status, glyph, label) => {
    const { container } = render(<StatusBadge status={status} />);
    expect(container.textContent).toBe(`${glyph}${label}`);
  });
});
