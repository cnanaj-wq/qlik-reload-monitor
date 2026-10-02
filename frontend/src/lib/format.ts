// Formats d'affichage.
//
// Convention de fuseau horaire : l'API renvoie des horodatages ISO SANS fuseau,
// qui sont l'heure locale de la machine exécutant le monitor (celle des logs
// Qlik). Ils sont affichés tels quels, par simple découpage de la chaîne,
// sans aucune conversion. Seul le calcul d'une durée « en cours » compare avec
// l'horloge du navigateur, supposé dans le même fuseau (usage local).

export function fmtInt(n: number): string {
  const s = Math.round(n).toString();
  return s.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
}

export function fmtRows(n: number): string {
  return `${fmtInt(n)} ${Math.abs(n) > 1 ? "lignes" : "ligne"}`;
}

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

/** Unités binaires, comme l'Explorateur Windows : 838 KB, 4.6 MB, 31.8 MB, 1.4 GB. */
export function fmtBytes(n: number): string {
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (a >= GB) return `${sign}${(a / GB).toFixed(1)} GB`;
  if (a >= MB) return `${sign}${(a / MB).toFixed(1)} MB`;
  if (a >= KB) return `${sign}${Math.round(a / KB)} KB`;
  return `${sign}${a} B`;
}

export function fmtDelta(n: number): string {
  return n > 0 ? `+${fmtBytes(n)}` : fmtBytes(n);
}

/** 832 ms · 3.2 s · 47.8 s · 1m 03s · 12m 41s · 1h 02m */
export function fmtDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

/** Chronomètre 00:01:04 (en-tête LIVE). */
export function fmtClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?$/;

/** 2026-10-01T22:47:15.975551 -> 01/10/2026 22:47:15 (sans conversion). */
export function fmtDateTime(ts: string | null | undefined): string {
  const m = ts ? ISO.exec(ts) : null;
  if (!m) return ts ?? "—";
  return `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}:${m[6]}`;
}

/** 2026-10-01T22:47:15.975551 -> 22:47:15 */
export function fmtTime(ts: string | null | undefined): string {
  const m = ts ? ISO.exec(ts) : null;
  return m ? `${m[4]}:${m[5]}:${m[6]}` : (ts ?? "—");
}

/** Interprète un horodatage local (fraction tronquée à la milliseconde). */
export function parseLocal(ts: string): Date {
  const m = ISO.exec(ts);
  if (!m) return new Date(Number.NaN);
  const ms = m[7] ? Number(m[7].slice(1, 4).padEnd(3, "0")) : 0;
  return new Date(+m[1]!, +m[2]! - 1, +m[3]!, +m[4]!, +m[5]!, +m[6]!, ms);
}

export function msBetween(from: string, to: string): number {
  return parseLocal(to).getTime() - parseLocal(from).getTime();
}
