import { memo, useEffect, useMemo, useRef, useState } from "react";
import { buildTimeline, errorParts, LONG_MESSAGE, type TimelineItem } from "../lib/timeline";
import { TONE_TEXT } from "../lib/tone";
import type { ReloadEvent } from "../types";

/** Une ligne de timeline. Mémorisée : seules les nouvelles lignes sont rendues. */
export const TimelineEvent = memo(function TimelineEvent({ item }: { item: TimelineItem }) {
  const isError = item.event.event_type === "ERROR";
  const isWarning = item.event.event_type === "WARNING";
  const [expanded, setExpanded] = useState(false);
  const parts = isError ? errorParts(item.event.message) : null;
  const long = (item.event.message?.length ?? 0) > LONG_MESSAGE;

  return (
    <li
      className={`group relative grid grid-cols-[4.5rem_1.25rem_minmax(0,1fr)] items-baseline gap-x-2 pr-3 ${
        item.minor ? "py-0.5" : "py-1.5"
      } ${isError ? "rounded-md bg-err/10" : isWarning ? "rounded-md bg-warn/8" : ""}`}
      data-seq={item.seq}
      data-type={item.event.event_type}
    >
      <time className="num pl-2 text-[12px] text-muted">{item.time}</time>
      <span aria-hidden="true" className="relative z-10 flex justify-center">
        {item.glyph && (
          <span className={`rounded-full bg-panel px-0.5 text-[13px] leading-5 ${TONE_TEXT[item.tone]}`}>
            {item.glyph}
          </span>
        )}
      </span>
      <div className="min-w-0">
        {isError && parts ? (
          <div>
            <p className="font-semibold text-err">
              ERROR
              {item.event.table && <span className="ml-2 font-normal text-fg">{item.event.table}</span>}
            </p>
            {parts.code && <p className="num font-semibold text-fg">{parts.code}</p>}
            <p className={`text-fg ${expanded ? "whitespace-pre-wrap break-words" : "line-clamp-2"}`}>
              {expanded ? item.event.message : parts.text}
            </p>
            {long && (
              <button
                className="mt-0.5 text-[12px] text-run underline-offset-2 hover:underline"
                onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}
              >
                {expanded ? "Masquer le détail" : "Afficher le détail"}
              </button>
            )}
          </div>
        ) : (
          <p className={item.minor ? "num text-[13px] text-muted" : "font-medium"}>
            <span className={isWarning ? "text-warn" : undefined}>{item.title}</span>
            {item.detail && (
              <span className={`num ml-2 font-normal ${item.minor ? "text-muted/80" : "text-muted"}`}>
                {item.detail}
              </span>
            )}
          </p>
        )}
      </div>
    </li>
  );
});

interface LiveTimelineProps {
  events: ReloadEvent[];
  /** Suit automatiquement les nouveaux événements (si l'utilisateur est en bas de liste). */
  follow?: boolean;
  className?: string;
}

export function LiveTimeline({ events, follow = true, className = "" }: LiveTimelineProps) {
  const items = useMemo(() => buildTimeline(events), [events]);
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (follow && el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [items.length, follow]);

  if (items.length === 0) {
    return <p className="px-4 py-6 text-muted">Aucun événement pour ce reload.</p>;
  }

  return (
    <div
      ref={scroller}
      className={`scroll-quiet overflow-y-auto ${className}`}
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      <ol aria-label="Chronologie du reload" className="relative py-2">
        <span aria-hidden="true" className="absolute bottom-3 left-[5.625rem] top-3 w-px bg-line" />
        {items.map((item) => (
          <TimelineEvent key={item.seq} item={item} />
        ))}
      </ol>
    </div>
  );
}
