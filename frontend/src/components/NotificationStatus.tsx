import { fmtTime } from "../lib/format";
import type { NotificationRecord } from "../types";

interface Props {
  records: NotificationRecord[];
  /** Texte affiché quand aucune notification n'existe (sinon rien n'est affiché). */
  emptyHint?: string | null;
}

/** Notification email d'un reload : envoyée, échouée, simulée (dry-run) ou en cours. */
export function NotificationStatus({ records, emptyHint = null }: Props) {
  if (records.length === 0) {
    if (!emptyHint) return null;
    return (
      <section aria-label="Notifications" className="rounded-lg border border-line bg-ink/40 px-4 py-2.5">
        <h5 className="text-[12px] text-muted">Notification email</h5>
        <p className="mt-1 text-[13px] text-muted" data-testid="notification-empty">
          {emptyHint}
        </p>
      </section>
    );
  }
  return (
    <section aria-label="Notifications" className="rounded-lg border border-line bg-ink/40 px-4 py-2.5">
      <h5 className="text-[12px] text-muted">Notification email</h5>
      <ul className="mt-1 space-y-1">
        {records.map((n) => (
          <li key={n.id} className="text-[13px]" data-testid="notification" data-status={n.status}>
            {n.status === "SENT" && (
              <span className="text-ok">
                ✓ envoyée à {n.recipient} à <span className="num">{fmtTime(n.sent_at)}</span>
                {n.attempts > 1 && <span className="text-muted"> ({n.attempts} tentatives)</span>}
              </span>
            )}
            {n.status === "FAILED" && (
              <span className="text-err">
                ✕ échec d'envoi à {n.recipient} ({n.attempts} tentative{n.attempts > 1 ? "s" : ""})
                <span className="block pl-4 text-muted">
                  {n.error_message ?? "Cause non précisée."} L'erreur du reload n'a pas été notifiée par email.
                </span>
              </span>
            )}
            {n.status === "DRY_RUN" && (
              <span className="text-muted">
                ○ simulée (dry-run), non envoyée — destinataire {n.recipient}
                <span className="block pl-4">
                  Message construit à <span className="num">{fmtTime(n.created_at)}</span>, aucun email n'est parti
                  (dry_run actif).
                </span>
              </span>
            )}
            {n.status === "PENDING" && (
              <span className="text-warn">
                … envoi en cours à {n.recipient}
                {n.attempts > 0 && ` (tentative ${n.attempts + 1})`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
