import { fmtTime } from "../lib/format";
import type { NotificationRecord } from "../types";

/** Notification email d'un reload : envoyée, échouée, simulée (dry-run) ou en cours. */
export function NotificationStatus({ records }: { records: NotificationRecord[] }) {
  if (records.length === 0) return null;
  return (
    <section aria-label="Notifications" className="rounded-lg border border-line bg-ink/40 px-4 py-2.5">
      <h5 className="text-[12px] text-muted">Notification email</h5>
      <ul className="mt-1 space-y-1">
        {records.map((n) => (
          <li key={n.id} className="text-[13px]" data-testid="notification" data-status={n.status}>
            {n.status === "SENT" && (
              <span className="text-ok">
                ✓ envoyée à {n.recipient} à <span className="num">{fmtTime(n.sent_at)}</span>
              </span>
            )}
            {n.status === "FAILED" && (
              <span className="text-err">
                ✕ échec d'envoi à {n.recipient} ({n.attempts} tentative{n.attempts > 1 ? "s" : ""})
                {n.error_message && <span className="block pl-4 text-muted">{n.error_message}</span>}
              </span>
            )}
            {n.status === "DRY_RUN" && (
              <span className="text-muted">
                ○ simulée (dry-run), non envoyée — destinataire {n.recipient}
              </span>
            )}
            {n.status === "PENDING" && <span className="text-warn">… envoi en cours à {n.recipient}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}
