import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { NotificationStatus } from "../components/NotificationStatus";
import { ReloadHistory } from "../components/ReloadHistory";
import { clearDetailCache } from "../hooks/useReloadHistory";
import { ev, notification, page, resetSeq, state, summary } from "../test/fixtures";

vi.mock("../api/client", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../api/client")>();
  return {
    ...mod,
    getReloads: vi.fn(),
    getReload: vi.fn(),
    getReloadEvents: vi.fn(),
    getQvdMeasures: vi.fn(),
    getNotificationStatus: vi.fn(),
  };
});
const api = vi.mocked(client);

function errorReloadEvents() {
  resetSeq(0);
  const r = { reload_id: "E1" };
  return [
    ev({ ...r, event_type: "RELOAD_START" }),
    ev({ ...r, event_type: "SECTION_START", section: "PARAMETRES" }),
    ev({ ...r, event_type: "WARNING", status: "WARNING", section: "PARAMETRES", message: "Variable vDateDebut non définie" }),
    ev({ ...r, event_type: "SECTION_END", section: "PARAMETRES", status: "WARNING" }),
    ev({ ...r, event_type: "SECTION_START", section: "DIMENSIONS" }),
    ev({ ...r, event_type: "TABLE_START", section: "DIMENSIONS", table: "CLIENTS" }),
    ev({ ...r, event_type: "TABLE_END", section: "DIMENSIONS", table: "CLIENTS", rows: 42318, status: "SUCCESS" }),
    ev({ ...r, event_type: "QVD_WRITE_START", section: "DIMENSIONS", qvd: "CLIENTS.qvd" }),
    ev({ ...r, event_type: "QVD_SIZE_CHANGE", qvd: "CLIENTS.qvd", qvd_size_bytes: 4_823_449 }),
    ev({ ...r, event_type: "QVD_STABLE", qvd: "CLIENTS.qvd", qvd_size_bytes: 4_823_449, status: "SUCCESS" }),
    ev({ ...r, event_type: "SECTION_END", section: "DIMENSIONS", status: "SUCCESS" }),
    ev({ ...r, event_type: "SECTION_START", section: "FAITS" }),
    ev({ ...r, event_type: "ERROR", status: "ERROR", section: "FAITS", table: "VENTES", message: "ORA-00942: table or view does not exist" }),
    ev({ ...r, event_type: "WARNING", status: "WARNING", message: "Événement sans section" }),
    ev({ ...r, event_type: "RELOAD_END", status: "ERROR" }),
  ];
}

const ROWS = [
  summary({ reload_id: "E1", status: "ERROR", platform: "qlik_sense", app_name: "VENTES", errors_count: 1, total_rows: 737022 }),
  summary({ reload_id: "V1", status: "SUCCESS", platform: "qlik_view", app_name: "VENTES_FINANCE.qvw", started_at: "2026-10-01T21:00:00" }),
  summary({ reload_id: "D1", status: "WARNING", platform: "demo", app_name: "Ventes", started_at: "2026-10-01T20:00:00" }),
];

function mockDetail(notif = [notification({ reload_id: "E1" })]) {
  api.getReload.mockResolvedValue(
    state({ reload_id: "E1", status: "ERROR", is_running: false, ended_at: "2026-10-01T22:17:00", elapsed_ms: 78000, total_rows: 1887542, warnings_count: 2, errors_count: 1 }),
  );
  api.getReloadEvents.mockResolvedValue(errorReloadEvents());
  api.getQvdMeasures.mockResolvedValue([]);
  api.getNotificationStatus.mockResolvedValue(notif);
}

beforeEach(() => {
  clearDetailCache();
  api.getReloads.mockResolvedValue(page(ROWS, 45));
  mockDetail();
});

function row(id: string) {
  return document.querySelector(`li[data-reload="${id}"]`) as HTMLElement;
}

describe("historique", () => {
  it("une ligne par occurrence, repliée par défaut, sans charger les détails", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    expect(within(row("E1")).getByText("01/10/2026 22:15:42")).toBeInTheDocument();
    expect(within(row("E1")).getByText("ERROR")).toBeInTheDocument();
    expect(within(row("E1")).getByText("1m 18s")).toBeInTheDocument();
    expect(within(row("E1")).getByText("737 022")).toBeInTheDocument();
    expect(within(row("V1")).getByText("VIEW", { selector: "[data-platform]" })).toBeInTheDocument();
    expect(within(row("D1")).getByText("DEMO", { selector: "[data-platform]" })).toBeInTheDocument();
    expect(within(row("E1")).getByText("SENSE", { selector: "[data-platform]" })).toBeInTheDocument();
    for (const id of ["E1", "V1", "D1"]) {
      expect(within(row(id)).getAllByRole("button")[0]).toHaveAttribute("aria-expanded", "false");
    }
    expect(api.getReloadEvents).not.toHaveBeenCalled(); // lazy loading
    expect(api.getReloads).toHaveBeenCalledWith(
      { platform: "", app: "", status: "", dateFrom: "", dateTo: "" }, 0, expect.anything());
  });

  it("accordéon reload : chargement à l'ouverture, un seul ouvert, tout réduire", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    const user = userEvent.setup();

    await user.click(within(row("E1")).getAllByRole("button")[0]!);
    expect(await screen.findByTestId("reload-detail")).toBeInTheDocument();
    expect(api.getReload).toHaveBeenCalledWith("E1", expect.anything());
    expect(api.getReloadEvents).toHaveBeenCalledWith("E1", 0, expect.anything());
    expect(api.getQvdMeasures).toHaveBeenCalledWith("E1", expect.anything());
    expect(api.getNotificationStatus).toHaveBeenCalledWith("E1", expect.anything());

    const detail = screen.getByTestId("reload-detail");
    expect(within(detail).getByText("1m 18s")).toBeInTheDocument();
    expect(within(detail).getByText("1 887 542")).toBeInTheDocument();

    // Un seul reload ouvert à la fois
    await user.click(within(row("V1")).getAllByRole("button")[0]!);
    await waitFor(() => expect(within(row("E1")).getAllByRole("button")[0]).toHaveAttribute("aria-expanded", "false"));
    expect(within(row("V1")).getAllByRole("button")[0]).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Tout réduire" }));
    expect(screen.queryByTestId("reload-detail")).not.toBeInTheDocument();
  });

  it("détail en cache : un reload terminé n'est pas rechargé", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    const btn = () => within(row("E1")).getAllByRole("button")[0]!;
    fireEvent.click(btn());
    await screen.findByTestId("reload-detail");
    fireEvent.click(btn());
    fireEvent.click(btn());
    await screen.findByTestId("reload-detail");
    expect(api.getReloadEvents).toHaveBeenCalledTimes(1);
  });

  it("accordéon sections : erreurs dépliées, autres repliées, tout déplier / réduire", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    fireEvent.click(within(row("E1")).getAllByRole("button")[0]!);
    const detail = await screen.findByTestId("reload-detail");

    const section = (name: string) =>
      detail.querySelector(`li[data-section="${name}"] > button`) as HTMLElement;
    expect(section("PARAMETRES")).toHaveAttribute("aria-expanded", "true"); // warning
    expect(section("DIMENSIONS")).toHaveAttribute("aria-expanded", "false");
    expect(section("FAITS")).toHaveAttribute("aria-expanded", "true"); // erreur
    expect(section("AUTRES ÉVÉNEMENTS")).toBeTruthy(); // jamais masqué

    fireEvent.click(section("DIMENSIONS"));
    expect(section("DIMENSIONS")).toHaveAttribute("aria-expanded", "true");
    const dims = detail.querySelector('li[data-section="DIMENSIONS"]') as HTMLElement;
    expect(within(dims).getByText("42 318 lignes", { exact: false })).toBeInTheDocument();
    expect(within(dims).getByText("CLIENTS.qvd stabilisé")).toBeInTheDocument();

    fireEvent.click(within(detail).getByRole("button", { name: "Réduire toutes les sections" }));
    for (const s of ["PARAMETRES", "DIMENSIONS", "FAITS", "AUTRES ÉVÉNEMENTS"]) {
      expect(section(s)).toHaveAttribute("aria-expanded", "false");
    }
    fireEvent.click(within(detail).getByRole("button", { name: "Déplier toutes les sections" }));
    for (const s of ["PARAMETRES", "DIMENSIONS", "FAITS", "AUTRES ÉVÉNEMENTS"]) {
      expect(section(s)).toHaveAttribute("aria-expanded", "true");
    }
  });

  it("erreur et warnings visibles dans le détail", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    fireEvent.click(within(row("E1")).getAllByRole("button")[0]!);
    const detail = await screen.findByTestId("reload-detail");
    const card = within(detail).getByTestId("error-card");
    expect(within(card).getByText("ORA-00942")).toBeInTheDocument();
    expect(within(detail).getAllByTestId("warning-card")).toHaveLength(2);
  });

  it("filtres : plateforme, statut, dates, application (avec délai de saisie)", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    const form = screen.getByRole("search");
    fireEvent.change(within(form).getByLabelText("Plateforme"), { target: { value: "qlik_view" } });
    await waitFor(() =>
      expect(api.getReloads).toHaveBeenLastCalledWith(expect.objectContaining({ platform: "qlik_view" }), 0, expect.anything()),
    );
    fireEvent.change(within(form).getByLabelText("Statut"), { target: { value: "ERROR" } });
    fireEvent.change(within(form).getByLabelText("Date début"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(form).getByLabelText("Date fin"), { target: { value: "2026-10-02" } });
    await waitFor(() =>
      expect(api.getReloads).toHaveBeenLastCalledWith(
        { platform: "qlik_view", app: "", status: "ERROR", dateFrom: "2026-10-01", dateTo: "2026-10-02" }, 0, expect.anything()),
    );
    const calls = api.getReloads.mock.calls.length;
    fireEvent.change(within(form).getByLabelText("Application / document"), { target: { value: "VEN" } });
    expect(api.getReloads.mock.calls.length).toBe(calls); // pas encore : délai de saisie
    await waitFor(() =>
      expect(api.getReloads).toHaveBeenLastCalledWith(expect.objectContaining({ app: "VEN" }), 0, expect.anything()),
    );
    fireEvent.click(within(form).getByRole("button", { name: "Effacer les filtres" }));
    await waitFor(() =>
      expect(api.getReloads).toHaveBeenLastCalledWith(
        { platform: "", app: "", status: "", dateFrom: "", dateTo: "" }, 0, expect.anything()),
    );
  });

  it("pagination : 20 par page, Précédent / Suivant", async () => {
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    const prev = screen.getByRole("button", { name: "Précédent" });
    const next = screen.getByRole("button", { name: "Suivant" });
    expect(prev).toBeDisabled();
    expect(screen.getByText(/1–20 sur 45 reloads · page 1\/3/)).toBeInTheDocument();
    fireEvent.click(next);
    await waitFor(() => expect(api.getReloads).toHaveBeenLastCalledWith(expect.anything(), 1, expect.anything()));
    fireEvent.click(screen.getByRole("button", { name: "Précédent" }));
    await waitFor(() => expect(api.getReloads).toHaveBeenLastCalledWith(expect.anything(), 0, expect.anything()));
  });

  it("aucun résultat", async () => {
    api.getReloads.mockResolvedValue(page([], 0));
    render(<ReloadHistory tick={0} />);
    expect(await screen.findByText("Aucun reload ne correspond à ces critères.")).toBeInTheDocument();
  });

  it("historique indisponible : message clair", async () => {
    api.getReloads.mockRejectedValue(new client.ApiError("API injoignable", null));
    render(<ReloadHistory tick={0} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Historique indisponible");
  });

  it("se rafraîchit à chaque début / fin de reload", async () => {
    const { rerender } = render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(api.getReloads).toHaveBeenCalledTimes(1));
    rerender(<ReloadHistory tick={1} />);
    await waitFor(() => expect(api.getReloads).toHaveBeenCalledTimes(2));
  });
});

describe("notifications", () => {
  it("notification envoyée", () => {
    render(<NotificationStatus records={[notification()]} />);
    expect(screen.getByTestId("notification")).toHaveTextContent("✓ envoyée à ${ALERT_EMAIL_RECIPIENT} à 22:47:23");
  });

  it("notification échouée", () => {
    render(
      <NotificationStatus
        records={[notification({ status: "FAILED", attempts: 3, sent_at: null, error_message: "SMTPServerDisconnected: connexion perdue" })]}
      />,
    );
    const el = screen.getByTestId("notification");
    expect(el).toHaveTextContent("✕ échec d'envoi à ${ALERT_EMAIL_RECIPIENT} (3 tentatives)");
    expect(el).toHaveTextContent("connexion perdue");
  });

  it("dry-run et absence de notification", () => {
    const { container, rerender } = render(<NotificationStatus records={[notification({ status: "DRY_RUN", sent_at: null })]} />);
    expect(screen.getByTestId("notification")).toHaveTextContent("simulée (dry-run), non envoyée");
    rerender(<NotificationStatus records={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("affichée dans le détail du reload", async () => {
    mockDetail([notification({ reload_id: "E1", status: "FAILED", attempts: 3, error_message: "authentification SMTP refusée (535)" })]);
    render(<ReloadHistory tick={0} />);
    await waitFor(() => expect(row("E1")).toBeTruthy());
    act(() => within(row("E1")).getAllByRole("button")[0]!.click());
    const detail = await screen.findByTestId("reload-detail");
    expect(within(detail).getByTestId("notification")).toHaveAttribute("data-status", "FAILED");
  });
});


