import { useState } from "react";
import { actionLabel, type SavedDecision } from "../../lib/contracts";
import { decisionLabel, doneLabel, shortDate } from "./format";

type Props = {
  decisions: SavedDecision[];
  onOpen: (decision: SavedDecision) => void;
  onClose: () => void;
};

export default function Register({ decisions, onOpen, onClose }: Props) {
  const [workflowFilter, setWorkflowFilter] = useState<string>("all");
  const [notConfirmedOnly, setNotConfirmedOnly] = useState<boolean>(false);

  const filtered = decisions.filter(d => {
    if (workflowFilter !== "all") {
      if (workflowFilter === "unknown") {
        if (d.workflow) return false;
      } else if (d.workflow !== workflowFilter) {
        return false;
      }
    }
    if (notConfirmedOnly && d.confirmed_action) {
      return false;
    }
    return true;
  });

  return (
    <div className="register" aria-label="Decisions register" role="region" style={{ maxWidth: 880, margin: "0 auto", padding: "16px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>Decisions Register</h2>
          <span className="cap" style={{ color: "var(--muted)" }}>
            Showing {filtered.length} of {decisions.length} decision{decisions.length === 1 ? "" : "s"}
          </span>
        </div>
        <button type="button" className="btn secondary small" onClick={onClose} aria-label="Close register">
          Close register
        </button>
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 20, flexWrap: "wrap", padding: "12px 16px", background: "var(--raised)", borderRadius: 8, border: "1px solid var(--rule)" }}>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <span className="lbl" style={{ color: "var(--text-2)" }}>Workflow:</span>
          <select
            className="in"
            aria-label="Filter by workflow"
            value={workflowFilter}
            onChange={e => setWorkflowFilter(e.target.value)}
            style={{ minHeight: 32, padding: "4px 8px" }}
          >
            <option value="all">All workflows</option>
            <option value="review">Portfolio review</option>
            <option value="rebalance">Rebalance</option>
            <option value="stock">Stock analysis</option>
            <option value="new_cash">New cash</option>
            <option value="theme">Theme</option>
            <option value="unknown">Unknown workflow</option>
          </select>
        </label>

        <label className="check" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, cursor: "pointer" }}>
          <input
            type="checkbox"
            aria-label="Filter not confirmed"
            checked={notConfirmedOnly}
            onChange={e => setNotConfirmedOnly(e.target.checked)}
          />
          <span>Not confirmed</span>
        </label>
      </div>

      {filtered.length === 0 ? (
        <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--muted)", border: "1px dashed var(--rule)", borderRadius: 8 }}>
          No decisions match the selected filters.
        </div>
      ) : (
        <div className="table-scroll">
          <table className="matrix register-table" aria-label="Decisions table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th scope="col" style={{ textAlign: "left" }}>Date</th>
                <th scope="col" style={{ textAlign: "left" }}>Workflow · Subject</th>
                <th scope="col" style={{ textAlign: "left" }}>Conclusion</th>
                <th scope="col" style={{ textAlign: "left" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(decision => {
                const label = decisionLabel(decision);
                const status = decision.confirmed_action
                  ? `You did: ${doneLabel(decision.confirmed_action.action)}`
                  : "Not confirmed";
                return (
                  <tr
                    key={decision.id}
                    data-testid="decision-row"
                    onClick={() => onOpen(decision)}
                    style={{ cursor: "pointer" }}
                    className="register-row"
                  >
                    <td style={{ textAlign: "left", whiteSpace: "nowrap" }}>
                      {shortDate(decision.saved_at)}
                    </td>
                    <td style={{ textAlign: "left" }}>
                      <span style={{ fontWeight: 500 }}>{label}</span>
                    </td>
                    <td style={{ textAlign: "left" }}>
                      {actionLabel(decision.conclusion.preferred_action)}
                    </td>
                    <td style={{ textAlign: "left", whiteSpace: "nowrap" }}>
                      {decision.confirmed_action ? (
                        <span>{status}</span>
                      ) : (
                        <span style={{ color: "var(--amber)" }}>
                          <span className="dot" style={{ display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: "var(--amber)", marginRight: 6 }} />
                          Not confirmed
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
