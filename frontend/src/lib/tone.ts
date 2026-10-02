// Classes Tailwind par ton (chaînes complètes, détectables par Tailwind).
export type Tone = "running" | "success" | "warning" | "error" | "waiting" | "neutral";

export const TONE_TEXT: Record<Tone, string> = {
  running: "text-run",
  success: "text-ok",
  warning: "text-warn",
  error: "text-err",
  waiting: "text-wait",
  neutral: "text-muted",
};

export const TONE_CHIP: Record<Tone, string> = {
  running: "bg-run/12 text-run ring-run/35",
  success: "bg-ok/12 text-ok ring-ok/35",
  warning: "bg-warn/12 text-warn ring-warn/35",
  error: "bg-err/14 text-err ring-err/40",
  waiting: "bg-wait/10 text-wait ring-wait/30",
  neutral: "bg-raised text-muted ring-line",
};

export const TONE_RAIL: Record<Tone, string> = {
  running: "bg-run",
  success: "bg-ok",
  warning: "bg-warn",
  error: "bg-err",
  waiting: "bg-wait",
  neutral: "bg-line",
};
