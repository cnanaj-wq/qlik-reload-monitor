// Dérivations à partir des événements normalisés : fusion, timeline, sections, QVD.
// Fonctions pures, testées indépendamment des composants.
import type { EventStatus, ReloadEvent } from "../types";
import { fmtBytes, fmtDelta, fmtDuration, fmtRows, fmtTime, msBetween } from "./format";
import { worstStatus } from "./status";

// ------------------------------------------------------------------ fusion

/** Fusionne sans doublon (clé : seq) et trie par seq, l'ordre de référence. */
export function mergeEvents(current: ReloadEvent[], incoming: ReloadEvent[]): ReloadEvent[] {
  if (incoming.length === 0) return current;
  const lastSeq = current.length ? current[current.length - 1]!.seq : -Infinity;
  const ordered = incoming.every((e, i) => e.seq > (i === 0 ? lastSeq : incoming[i - 1]!.seq));
  if (ordered) return current.concat(incoming); // cas courant : flux SSE ordonné
  const bySeq = new Map<number, ReloadEvent>();
  for (const e of current) bySeq.set(e.seq, e);
  for (const e of incoming) if (!bySeq.has(e.seq)) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

// ---------------------------------------------------------------- timeline

export type TimelineTone = "running" | "success" | "warning" | "error" | "neutral";

export interface TimelineItem {
  seq: number;
  time: string; // HH:MM:SS
  minor: boolean; // ligne secondaire (progression, taille)
  glyph: string;
  tone: TimelineTone;
  title: string;
  detail: string | null;
  event: ReloadEvent;
}

const END_TONE: Record<EventStatus, TimelineTone> = {
  SUCCESS: "success",
  WARNING: "warning",
  ERROR: "error",
  RUNNING: "running",
  PENDING: "neutral",
};

export function buildTimeline(events: ReloadEvent[]): TimelineItem[] {
  const sectionStart = new Map<string, string>();
  const tableStart = new Map<string, string>();
  const qvdLastSize = new Map<string, number>();
  let reloadStart: string | null = null;
  const items: TimelineItem[] = [];

  for (const e of events) {
    const base = { seq: e.seq, time: fmtTime(e.timestamp), event: e, minor: false, detail: null };
    switch (e.event_type) {
      case "RELOAD_START":
        reloadStart = e.timestamp;
        items.push({ ...base, glyph: "◆", tone: "running", title: "Reload démarré" });
        break;
      case "SECTION_START":
        sectionStart.set(e.section ?? "", e.timestamp);
        items.push({ ...base, glyph: "▶", tone: "neutral", title: e.section ?? "Section" });
        break;
      case "SECTION_END": {
        const start = sectionStart.get(e.section ?? "");
        items.push({
          ...base,
          glyph: e.status === "WARNING" ? "▲" : "✓",
          tone: END_TONE[e.status],
          title: e.section ?? "Section",
          detail: start ? fmtDuration(msBetween(start, e.timestamp)) : null,
        });
        break;
      }
      case "TABLE_START":
        tableStart.set(e.table ?? "", e.timestamp);
        items.push({ ...base, glyph: "⧗", tone: "running", title: e.table ?? "Table" });
        break;
      case "TABLE_PROGRESS":
        items.push({
          ...base,
          minor: true,
          glyph: "",
          tone: "neutral",
          title: e.rows !== null ? fmtRows(e.rows) : "progression",
        });
        break;
      case "TABLE_END": {
        const start = tableStart.get(e.table ?? "");
        const parts = [
          e.rows !== null ? fmtRows(e.rows) : null,
          start ? fmtDuration(msBetween(start, e.timestamp)) : null,
        ].filter(Boolean);
        items.push({
          ...base,
          glyph: "✓",
          tone: "success",
          title: e.table ?? "Table",
          detail: parts.join(" · ") || null,
        });
        break;
      }
      case "QVD_WRITE_START":
        qvdLastSize.set(e.qvd ?? "", 0);
        items.push({ ...base, glyph: "⤓", tone: "neutral", title: e.qvd ?? "QVD" });
        break;
      case "QVD_SIZE_CHANGE": {
        const key = e.qvd ?? "";
        const size = e.qvd_size_bytes ?? 0;
        const prev = qvdLastSize.get(key) ?? 0;
        qvdLastSize.set(key, size);
        items.push({
          ...base,
          minor: true,
          glyph: "",
          tone: "neutral",
          title: fmtBytes(size),
          detail: size !== prev ? fmtDelta(size - prev) : null,
        });
        break;
      }
      case "QVD_WRITE_END":
        items.push({
          ...base,
          minor: true,
          glyph: "",
          tone: "neutral",
          title: `${e.qvd ?? "QVD"} écrit`,
        });
        break;
      case "QVD_STABLE":
        items.push({
          ...base,
          glyph: "✓",
          tone: "success",
          title: `${e.qvd ?? "QVD"} stabilisé`,
          detail: e.qvd_size_bytes !== null ? fmtBytes(e.qvd_size_bytes) : null,
        });
        break;
      case "WARNING":
        items.push({ ...base, glyph: "▲", tone: "warning", title: e.message ?? "Warning" });
        break;
      case "ERROR":
        items.push({ ...base, glyph: "✕", tone: "error", title: e.message ?? "Erreur" });
        break;
      case "RELOAD_END": {
        const title =
          e.status === "ERROR"
            ? "Reload en erreur"
            : e.status === "WARNING"
              ? "Reload terminé avec warnings"
              : "Reload terminé";
        items.push({
          ...base,
          glyph: e.status === "ERROR" ? "✕" : e.status === "WARNING" ? "▲" : "✓",
          tone: END_TONE[e.status],
          title,
          detail: reloadStart ? `Durée ${fmtDuration(msBetween(reloadStart, e.timestamp))}` : null,
        });
        break;
      }
    }
  }
  return items;
}

// ----------------------------------------------------------------- sections

export const OTHER_SECTION = "AUTRES ÉVÉNEMENTS";

export interface SectionGroup {
  key: string;
  name: string;
  isOther: boolean;
  events: ReloadEvent[];
  status: EventStatus;
  durationMs: number | null;
  startedAt: string | null;
}

export interface GroupedReload {
  head: ReloadEvent[]; // début de reload
  sections: SectionGroup[];
  tail: ReloadEvent[]; // fin de reload
}

/**
 * Niveau 2 de l'historique. Aucun événement n'est masqué :
 * - événements avec section -> leur section ;
 * - événements QVD sans section (mesures du watcher) -> section où le QVD a été écrit ;
 * - début / fin de reload -> en-tête / pied ;
 * - tout le reste -> « AUTRES ÉVÉNEMENTS ».
 */
export function groupBySection(events: ReloadEvent[], reloadRunning: boolean): GroupedReload {
  const head: ReloadEvent[] = [];
  const tail: ReloadEvent[] = [];
  const groups: SectionGroup[] = [];
  const open = new Map<string, SectionGroup>(); // section -> groupe courant
  const qvdSection = new Map<string, string>();
  let other: SectionGroup | null = null;

  const groupFor = (name: string, startsNew: boolean): SectionGroup => {
    const current = open.get(name);
    if (current && !(startsNew && current.events.some((x) => x.event_type === "SECTION_END"))) {
      return current;
    }
    const g: SectionGroup = {
      key: `${name}#${groups.length}`,
      name,
      isOther: false,
      events: [],
      status: "PENDING",
      durationMs: null,
      startedAt: null,
    };
    groups.push(g);
    open.set(name, g);
    return g;
  };

  for (const e of events) {
    if (e.event_type === "RELOAD_START") {
      head.push(e);
      continue;
    }
    if (e.event_type === "RELOAD_END") {
      tail.push(e);
      continue;
    }
    let section = e.section;
    if (!section && e.qvd) section = qvdSection.get(e.qvd) ?? null;
    if (section) {
      if (e.qvd && e.section) qvdSection.set(e.qvd, e.section);
      groupFor(section, e.event_type === "SECTION_START").events.push(e);
    } else {
      if (!other) {
        other = {
          key: "__other__",
          name: OTHER_SECTION,
          isOther: true,
          events: [],
          status: "PENDING",
          durationMs: null,
          startedAt: null,
        };
      }
      other.events.push(e);
    }
  }

  const all = other ? [...groups, other] : groups;
  for (const g of all) {
    const start = g.events.find((x) => x.event_type === "SECTION_START");
    const end = [...g.events].reverse().find((x) => x.event_type === "SECTION_END");
    g.startedAt = start?.timestamp ?? g.events[0]?.timestamp ?? null;
    g.durationMs = start && end ? msBetween(start.timestamp, end.timestamp) : null;
    const flagged = worstStatus(
      g.events.filter((x) => x.status === "ERROR" || x.status === "WARNING").map((x) => x.status),
    );
    if (flagged === "ERROR" || flagged === "WARNING") g.status = flagged;
    else if (end || g.isOther) g.status = "SUCCESS";
    else g.status = reloadRunning ? "RUNNING" : "PENDING";
  }
  return { head, sections: all, tail };
}

// --------------------------------------------------------------------- QVD

export type QvdPhase = "writing" | "written" | "stable";

export interface QvdSummary {
  name: string;
  path: string | null;
  phase: QvdPhase;
  sizeBytes: number | null;
  lastDelta: number | null;
  lastAt: string;
  /** Tailles observées pendant l'épisode d'écriture courant (courbe de croissance). */
  series: { at: string; size: number }[];
}

/**
 * État de chaque QVD, uniquement à partir des tailles observées.
 * Aucun pourcentage : la taille finale n'est jamais connue à l'avance.
 */
export function summarizeQvds(events: ReloadEvent[]): QvdSummary[] {
  const byName = new Map<string, QvdSummary>();
  const touch = (e: ReloadEvent): QvdSummary => {
    const name = e.qvd ?? "?";
    let q = byName.get(name);
    if (!q) {
      q = { name, path: e.qvd_path, phase: "writing", sizeBytes: null, lastDelta: null, lastAt: e.timestamp, series: [] };
      byName.set(name, q);
    }
    q.path = e.qvd_path ?? q.path;
    q.lastAt = e.timestamp;
    return q;
  };

  for (const e of events) {
    if (!e.qvd) continue;
    switch (e.event_type) {
      case "QVD_WRITE_START": {
        const q = touch(e);
        q.phase = "writing"; // nouvel épisode : la taille repart de zéro
        q.sizeBytes = null;
        q.lastDelta = null;
        q.series = [];
        break;
      }
      case "QVD_SIZE_CHANGE": {
        const q = touch(e);
        const size = e.qvd_size_bytes ?? 0;
        q.lastDelta = size - (q.sizeBytes ?? 0);
        q.sizeBytes = size;
        q.series.push({ at: e.timestamp, size });
        if (q.phase === "stable") q.phase = "writing";
        break;
      }
      case "QVD_WRITE_END": {
        const q = touch(e);
        if (q.phase !== "stable") q.phase = "written";
        break;
      }
      case "QVD_STABLE": {
        const q = touch(e);
        q.phase = "stable";
        if (e.qvd_size_bytes !== null) q.sizeBytes = e.qvd_size_bytes;
        break;
      }
      default:
        break;
    }
  }
  return [...byName.values()];
}

// -------------------------------------------------------------- erreurs

export interface ErrorParts {
  code: string | null;
  text: string;
}

/** « SQL##f … ORA-00942: table or view does not exist » -> code + description. */
export function errorParts(message: string | null): ErrorParts {
  if (!message) return { code: null, text: "Message non disponible" };
  const m = /\b([A-Z]{2,8}-\d{3,6})\b\s*:?\s*(.*)$/s.exec(message);
  if (m && m[1]) return { code: m[1], text: m[2]?.trim() || message };
  return { code: null, text: message };
}

export const LONG_MESSAGE = 160;

// ------------------------------------------------------------ étape courante

const STEP_LABEL: Partial<Record<string, (target: string) => string>> = {
  RELOAD_START: () => "Démarrage",
  RELOAD_END: () => "Terminé",
  SECTION_START: (t) => `Section ${t}`,
  SECTION_END: (t) => `Section ${t} terminée`,
  TABLE_START: (t) => `Chargement ${t}`,
  TABLE_PROGRESS: (t) => `Chargement ${t}`,
  TABLE_END: (t) => `${t} chargée`,
  QVD_WRITE_START: (t) => `Écriture ${t}`,
  QVD_SIZE_CHANGE: (t) => `Écriture ${t}`,
  QVD_WRITE_END: (t) => `${t} écrit`,
  QVD_STABLE: (t) => `${t} stabilisé`,
};

/** « TABLE_PROGRESS VENTES » (API) -> « Chargement VENTES ». */
export function stepLabel(step: string | null): string | null {
  if (!step) return null;
  const [type = "", ...rest] = step.split(" ");
  const target = rest.join(" ");
  const fn = STEP_LABEL[type];
  return fn ? fn(target).trim() : step;
}
