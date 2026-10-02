import { useEffect, useState } from "react";

/** Heure courante, rafraîchie chaque seconde uniquement si `active`. */
export function useNow(active: boolean, intervalMs = 1000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    if (!active) return;
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(t);
  }, [active, intervalMs]);
  return now;
}
