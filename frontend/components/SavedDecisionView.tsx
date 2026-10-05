"use client";

import { useState, type FormEvent } from "react";
import {
  actionLabel,
  valueLabel,
  post,
  type DecisionAction,
  type SavedDecision,
} from "../lib/contracts";

export default function SavedDecisionView({
  decision: initialDecision,
  onClose,
  onUpdated,
}: {
  decision: SavedDecision;
  onClose: () => void;
  onUpdated?: (updated: SavedDecision) => void;
}) {
  const [decision, setDecision] = useState<SavedDecision>(initialDecision);
  const [confirmAction, setConfirmAction] = useState<DecisionAction | "">("");
  const [confirmNotes, setConfirmNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleConfirm(event: FormEvent) {
    event.preventDefault();
    if (!confirmAction) return;
    setBusy(true);
    setError("");
    try {
      const updated = await post<SavedDecision>(`/api/decisions/${decision.id}/confirm`, {
        action: confirmAction,
        notes: confirmNotes.trim() || undefined,
      });
      setDecision(updated);
      if (onUpdated) onUpdated(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm action.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="review-result saved-decision-view" aria-label="Reopened saved decision">
      <div className="section-heading">
        <div>
          <p className="eyebrow">REVISITING HISTORICAL DECISION / AS OF {decision.as_of}</p>
          <h2>{actionLabel(decision.conclusion.preferred_action)}</h2>
        </div>
        <div className="button-group">
          <span className="badge" data-testid="historical-badge">Historical decision</span>
          <span className="badge" data-testid="confirmed-status-badge">
            {decision.confirmed_action ? "User confirmed" : "Unconfirmed"}
          </span>
          <button type="button" className="button" onClick={onClose} aria-label="Back to portfolio">
            ← Back to current analysis
          </button>
        </div>
      </div>

      <div className="panel answer-panel">
        <p className="qualification" data-testid="historical-date-warning">
          <strong>Historical decision as-of {decision.as_of} (saved {decision.saved_at}).</strong> Stored analysis and historical evidence; not a live or refreshed quote.
        </p>

        <div className="question-bubble" aria-label="Original dated question">
          <span className="eyebrow">ORIGINAL DATED QUESTION</span>
          <p>{decision.question}</p>
        </div>

        <h3>Conclusion</h3>
        <p className="answer-reason">
          <strong>Preferred action:</strong> {actionLabel(decision.conclusion.preferred_action)}
        </p>
        {decision.conclusion.amount ? (
          <p className="qualification">
            Approximate allocation: {valueLabel(decision.conclusion.amount.minimum, decision.conclusion.amount.currency)} to{" "}
            {valueLabel(decision.conclusion.amount.maximum, decision.conclusion.amount.currency)} for{" "}
            {decision.conclusion.amount.position_id}
          </p>
        ) : (
          <p className="muted small">Allocation amount: not determined</p>
        )}

        <section className="panel" aria-label="Action confirmation status">
          <h3>User-confirmed action</h3>
          {decision.confirmed_action ? (
            <div data-testid="confirmed-action-details">
              <p>
                <strong>Status:</strong> Confirmed user action
              </p>
              <p>
                <strong>Action:</strong> {actionLabel(decision.confirmed_action.action)}
              </p>
              <p className="muted small">
                Confirmed at: {decision.confirmed_action.confirmed_at}
              </p>
              {decision.confirmed_action.notes && (
                <p>
                  <strong>Notes:</strong> {decision.confirmed_action.notes}
                </p>
              )}
              <p className="muted small">
                Only explicitly user-confirmed actions are labeled confirmed. Recommendations never become claimed trades, and execution details are not inferred.
              </p>
            </div>
          ) : (
            <div data-testid="unconfirmed-action-details">
              <p>
                <strong>Status:</strong> Unconfirmed
              </p>
              <p className="muted small">
                Treat a recommendation as analysis. No action has been confirmed by the user. Execution details are not inferred.
              </p>
              <form onSubmit={handleConfirm} className="confirm-action-form">
                <label>
                  Record user-confirmed action
                  <select
                    value={confirmAction}
                    disabled={busy}
                    aria-label="Select confirmed action"
                    onChange={(e) => setConfirmAction(e.target.value as DecisionAction)}
                  >
                    <option value="" disabled>Select your confirmed action…</option>
                    <option value="no_action">No action</option>
                    <option value="add">Add</option>
                    <option value="hold">Hold</option>
                    <option value="reduce">Reduce</option>
                    <option value="exit">Exit</option>
                  </select>
                </label>
                <label>
                  Notes (optional)
                  <textarea
                    value={confirmNotes}
                    disabled={busy}
                    placeholder="e.g. Reviewed thesis, opted not to trade."
                    maxLength={1000}
                    onChange={(e) => setConfirmNotes(e.target.value)}
                  />
                </label>
                <button
                  type="submit"
                  className="button primary"
                  disabled={busy || !confirmAction}
                  aria-label="Confirm action button"
                >
                  {busy ? "Saving confirmation…" : "Confirm action"}
                </button>
                {error && <p className="error">{error}</p>}
              </form>
            </div>
          )}
        </section>

        <h3>Reasoning</h3>
        <p>{decision.reasoning.reason}</p>
        <p className="muted small">Downside: {decision.reasoning.downside}</p>

        {decision.reasoning.assumptions.length > 0 && (
          <div className="review-points">
            <h3>Assumptions</h3>
            <ul>
              {decision.reasoning.assumptions.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {decision.reasoning.uncertainty.length > 0 && (
          <div className="review-points">
            <h3>Uncertainty</h3>
            <ul>
              {decision.reasoning.uncertainty.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {decision.reasoning.what_could_change.length > 0 && (
          <div className="review-points">
            <h3>What could change the view</h3>
            <ul>
              {decision.reasoning.what_could_change.map((item, idx) => (
                <li key={idx}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        {decision.reasoning.alternatives.length > 0 && (
          <div className="review-points">
            <h3>Alternatives</h3>
            <ul>
              {decision.reasoning.alternatives.map((alt, idx) => (
                <li key={idx}>
                  <strong>{actionLabel(alt.action)}:</strong> {alt.reason}
                </li>
              ))}
            </ul>
          </div>
        )}

        <h3>Evidence references</h3>
        {decision.evidence_references.length > 0 ? (
          <ul className="evidence-list" aria-label="Evidence references list">
            {decision.evidence_references.map((ref) => (
              <li key={ref.id} data-testid={`evidence-ref-${ref.id}`}>
                <strong>{ref.title}</strong>
                <div>
                  <small>
                    Source: {ref.source} {ref.as_of ? `· As of / Published: ${ref.as_of}` : ""}
                  </small>
                </div>
                {ref.excerpt && <p className="muted small">{ref.excerpt}</p>}
                {ref.url && /^https?:\/\//.test(ref.url) && (
                  <div>
                    <a href={ref.url} target="_blank" rel="noreferrer">
                      View primary source ↗
                    </a>
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">No external evidence references recorded.</p>
        )}
      </div>
    </section>
  );
}
