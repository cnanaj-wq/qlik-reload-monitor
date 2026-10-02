import { PLATFORM_META, STATUS_META } from "../lib/status";
import { TONE_CHIP } from "../lib/tone";
import type { EventStatus, Platform } from "../types";

interface StatusBadgeProps {
  status: EventStatus;
  size?: "sm" | "lg";
  /** Halo animé pour un reload en cours (seule animation automatique de l'interface). */
  live?: boolean;
}

/** Statut toujours lisible sans la couleur : symbole + texte. */
export function StatusBadge({ status, size = "sm", live = false }: StatusBadgeProps) {
  const meta = STATUS_META[status];
  const sizing = size === "lg" ? "px-3 py-1 text-[15px] gap-2" : "px-2 py-0.5 text-xs gap-1.5";
  return (
    <span
      className={`inline-flex items-center rounded-md font-semibold ring-1 ring-inset ${sizing} ${TONE_CHIP[meta.tone]}`}
      data-status={status}
    >
      <span
        aria-hidden="true"
        className={`leading-none ${live && status === "RUNNING" ? "pulse-run rounded-full" : ""}`}
      >
        {meta.glyph}
      </span>
      {meta.label}
    </span>
  );
}

export function PlatformBadge({ platform }: { platform: Platform }) {
  const meta = PLATFORM_META[platform];
  const tone =
    platform === "qlik_sense"
      ? "text-[#7fc4a8] ring-[#7fc4a8]/40"
      : platform === "qlik_view"
        ? "text-[#b7a6e8] ring-[#b7a6e8]/40"
        : "text-muted ring-line";
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-px text-[11px] font-semibold tracking-wide ring-1 ring-inset ${tone}`}
      title={meta.label}
      data-platform={platform}
    >
      {meta.badge}
    </span>
  );
}

/** « Qlik Sense · VENTES » */
export function ReloadTitle({ platform, appName }: { platform: Platform; appName: string }) {
  return (
    <span>
      <span className="text-muted">{PLATFORM_META[platform].label} · </span>
      <span>{appName}</span>
    </span>
  );
}
