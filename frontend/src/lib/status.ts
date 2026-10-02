// Libellés et symboles des statuts et plateformes.
// Règle : un statut n'est JAMAIS signalé par la couleur seule ; il porte
// toujours un symbole distinct et un texte.
import type { EventStatus, Platform } from "../types";

export interface StatusMeta {
  label: string;
  glyph: string;
  tone: "running" | "success" | "warning" | "error" | "waiting";
}

export const STATUS_META: Record<EventStatus, StatusMeta> = {
  RUNNING: { label: "RUNNING", glyph: "●", tone: "running" },
  SUCCESS: { label: "SUCCESS", glyph: "✓", tone: "success" },
  WARNING: { label: "WARNING", glyph: "▲", tone: "warning" },
  ERROR: { label: "ERROR", glyph: "✕", tone: "error" },
  PENDING: { label: "WAITING", glyph: "○", tone: "waiting" },
};

export interface PlatformMeta {
  label: string; // Qlik Sense · VENTES
  badge: string; // SENSE
}

export const PLATFORM_META: Record<Platform, PlatformMeta> = {
  qlik_sense: { label: "Qlik Sense", badge: "SENSE" },
  qlik_view: { label: "QlikView", badge: "VIEW" },
  demo: { label: "DEMO", badge: "DEMO" },
};

export const PLATFORM_OPTIONS: { value: Platform | ""; label: string }[] = [
  { value: "", label: "Toutes" },
  { value: "qlik_sense", label: "Qlik Sense" },
  { value: "qlik_view", label: "QlikView" },
  { value: "demo", label: "DEMO" },
];

export const STATUS_OPTIONS: { value: EventStatus | ""; label: string }[] = [
  { value: "", label: "Tous" },
  { value: "SUCCESS", label: "SUCCESS" },
  { value: "WARNING", label: "WARNING" },
  { value: "ERROR", label: "ERROR" },
  { value: "RUNNING", label: "RUNNING" },
];

const SEVERITY: Record<EventStatus, number> = {
  PENDING: 0,
  SUCCESS: 1,
  RUNNING: 2,
  WARNING: 3,
  ERROR: 4,
};

/** Statut le plus grave d'une liste (ERROR > WARNING > RUNNING > SUCCESS > WAITING). */
export function worstStatus(statuses: EventStatus[]): EventStatus {
  return statuses.reduce<EventStatus>(
    (worst, s) => (SEVERITY[s] > SEVERITY[worst] ? s : worst),
    "PENDING",
  );
}
