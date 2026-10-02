import { memo, useMemo } from "react";
import { fmtBytes, fmtDelta, fmtTime, msBetween, parseLocal } from "../lib/format";
import { summarizeQvds, type QvdPhase, type QvdSummary } from "../lib/timeline";
import { TONE_CHIP } from "../lib/tone";
import type { ReloadEvent } from "../types";

const PHASE: Record<QvdPhase, { label: string; glyph: string; tone: keyof typeof TONE_CHIP }> = {
  writing: { label: "WRITING", glyph: "●", tone: "running" },
  written: { label: "ÉCRIT", glyph: "◌", tone: "neutral" },
  stable: { label: "STABLE", glyph: "✓", tone: "success" },
};

/** Courbe de croissance de la taille observée. Aucune échelle « 100 % » : la fin n'est pas connue. */
export function Sparkline({ series, phase }: { series: QvdSummary["series"]; phase: QvdPhase }) {
  const W = 132;
  const H = 30;
  const color = phase === "writing" ? "var(--color-run)" : phase === "stable" ? "var(--color-ok)" : "var(--color-muted)";
  if (series.length === 0) return <svg width={W} height={H} aria-hidden="true" />;
  const t0 = parseLocal(series[0]!.at).getTime();
  const xs = series.map((p) => parseLocal(p.at).getTime() - t0);
  const maxX = Math.max(1, ...xs);
  const maxY = Math.max(1, ...series.map((p) => p.size));
  const pts = series.map((p, i) => [2 + (xs[i]! / maxX) * (W - 4), H - 2 - (p.size / maxY) * (H - 4)] as const);
  const line = pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `2,${H - 2} ${line} ${pts.at(-1)![0].toFixed(1)},${H - 2}`;
  const last = pts.at(-1)!;
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden="true" className="shrink-0">
      <polygon points={area} fill={color} opacity={0.12} />
      <polyline points={line} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={last[0]} cy={last[1]} r={2.2} fill={color} />
    </svg>
  );
}

export const QvdCard = memo(function QvdCard({ qvd }: { qvd: QvdSummary }) {
  const meta = PHASE[qvd.phase];
  const first = qvd.series[0];
  const last = qvd.series.at(-1);
  const writeMs = first && last ? msBetween(first.at, last.at) : 0;
  const speed = qvd.phase === "writing" && first && last && writeMs > 0
    ? (last.size - first.size) / (writeMs / 1000)
    : null;
  return (
    <article
      className="rounded-lg border border-line bg-panel px-4 py-3"
      data-qvd={qvd.name}
      data-phase={qvd.phase}
      aria-label={`${qvd.name} ${meta.label}`}
    >
      <header className="flex items-center justify-between gap-3">
        <h4 className="truncate font-semibold" title={qvd.path ?? qvd.name}>
          {qvd.name}
        </h4>
        <span className={`inline-flex items-center gap-1.5 rounded px-1.5 py-px text-[11px] font-semibold ring-1 ring-inset ${TONE_CHIP[meta.tone]}`}>
          <span aria-hidden="true">{meta.glyph}</span>
          {meta.label}
        </span>
      </header>
      <div className="mt-2 flex items-end justify-between gap-3">
        <div>
          <p className="text-[12px] text-muted">{qvd.phase === "writing" ? "Taille actuelle" : "Taille"}</p>
          <p className="num text-[20px] font-semibold leading-tight">
            {qvd.sizeBytes !== null ? fmtBytes(qvd.sizeBytes) : "—"}
          </p>
        </div>
        <Sparkline series={qvd.series} phase={qvd.phase} />
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 text-[12px]">
        <div>
          <dt className="text-muted">Dernière variation</dt>
          <dd className="num">{qvd.lastDelta !== null ? fmtDelta(qvd.lastDelta) : "—"}</dd>
        </div>
        <div>
          <dt className="text-muted">Dernière mesure</dt>
          <dd className="num">{fmtTime(qvd.lastAt)}</dd>
        </div>
        {speed !== null && speed > 0 && (
          <div className="col-span-2 mt-1">
            <dt className="sr-only">Vitesse observée</dt>
            <dd className="num text-muted">Vitesse observée ≈ {fmtBytes(speed)}/s</dd>
          </div>
        )}
        {qvd.phase === "written" && (
          <div className="col-span-2 mt-1 text-muted">Écriture terminée, en attente de stabilisation</div>
        )}
      </dl>
    </article>
  );
});

export function QvdPanel({ events, className = "" }: { events: ReloadEvent[]; className?: string }) {
  const qvds = useMemo(() => summarizeQvds(events), [events]);
  return (
    <section aria-label="QVD du reload" className={className}>
      <h3 className="mb-2 flex items-baseline justify-between text-[13px] font-semibold text-muted">
        <span>QVD du reload</span>
        <span className="num font-normal">
          {qvds.filter((q) => q.phase === "stable").length}/{qvds.length} stabilisés
        </span>
      </h3>
      {qvds.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-5 text-muted">
          Aucun QVD écrit pour l'instant.
        </p>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
          {qvds.map((q) => (
            <QvdCard key={q.name} qvd={q} />
          ))}
        </div>
      )}
    </section>
  );
}
