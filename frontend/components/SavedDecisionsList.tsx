"use client";

import { actionLabel, type SavedDecision } from "../lib/contracts";

export default function SavedDecisionsList({
  decisions,
  onReopen,
  busy,
}: {
  decisions: SavedDecision[];
  onReopen: (decision: SavedDecision) => void;
  busy?: boolean;
}) {
  if (!decisions.length) {
    return (
      <section className="panel" aria-label="Saved decisions">
        <h2>Revisit past decisions</h2>
        <p className="muted">No saved decisions yet. Save a completed analysis to revisit its evidence and reasoning honestly.</p>
      </section>
    );
  }

  return (
    <section className="panel" aria-label="Saved decisions">
      <div className="section-heading">
        <div>
          <h2>Revisit past decisions</h2>
          <p className="muted small">
            Stored dated questions, historical evidence, reasoning, and confirmed actions.
          </p>
        </div>
        <span className="badge">{decisions.length} saved</span>
      </div>

      <div className="table-scroll">
        <table aria-label="Saved decisions table">
          <thead>
            <tr>
              <th>As-of date</th>
              <th>Question</th>
              <th>Conclusion</th>
              <th>Action status</th>
              <th>Saved on</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {decisions.map((decision) => (
              <tr key={decision.id} data-testid={`saved-decision-row-${decision.id}`}>
                <td>
                  <strong>{decision.as_of}</strong>
                  <small className="muted">{decision.id}</small>
                </td>
                <td>
                  <span>{decision.question}</span>
                </td>
                <td>
                  <strong>{actionLabel(decision.conclusion.preferred_action)}</strong>
                </td>
                <td>
                  <span className="badge">
                    {decision.confirmed_action
                      ? `Confirmed: ${actionLabel(decision.confirmed_action.action)}`
                      : "Unconfirmed"}
                  </span>
                </td>
                <td>
                  <small>{decision.saved_at}</small>
                </td>
                <td>
                  <button
                    type="button"
                    className="button"
                    disabled={busy}
                    onClick={() => onReopen(decision)}
                    aria-label={`Reopen decision ${decision.id}`}
                  >
                    Reopen →
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
