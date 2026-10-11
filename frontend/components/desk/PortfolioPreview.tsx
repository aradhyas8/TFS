"use client";

import { useEffect, useRef } from "react";
import type { PortfolioProposal } from "../../lib/contracts";

export type PortfolioPreviewProps = {
  proposal: PortfolioProposal;
  onConfirm: () => void | Promise<void>;
  onCancel: () => void | Promise<void>;
  confirming?: boolean;
  cancelling?: boolean;
  error?: string | null;
  isStale?: boolean;
};

export default function PortfolioPreview({
  proposal,
  onConfirm,
  onCancel,
  confirming = false,
  cancelling = false,
  error = null,
  isStale = false,
}: PortfolioPreviewProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  const stale = isStale || proposal.status === "stale";

  // Focus dialog / confirm button on mount and handle Escape key
  useEffect(() => {
    confirmBtnRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !confirming && !cancelling) {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirming, cancelling, onCancel]);

  const originLabel =
    proposal.origin === "manual"
      ? "Manual edit"
      : proposal.origin === "import"
        ? "Holdings import"
        : proposal.origin === "identify"
          ? "Identity correction"
          : "Action proposal";

  return (
    <div
      className="preview-overlay"
      role="presentation"
      onClick={e => {
        if (e.target === e.currentTarget && !confirming && !cancelling) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        className="preview-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="preview-title"
        aria-describedby="preview-desc"
        tabIndex={-1}
      >
        <header className="preview-head">
          <div>
            <h2 id="preview-title" style={{ fontSize: 18, fontWeight: 500, margin: 0 }}>
              Review portfolio changes
            </h2>
            <p id="preview-desc" className="cap" style={{ marginTop: 2 }}>
              {originLabel} ·{" "}
              {proposal.base_revision != null
                ? `Base revision ${proposal.base_revision}`
                : "Initial portfolio creation"}
            </p>
          </div>
          <button
            type="button"
            className="icon-btn panel-close"
            style={{ display: "inline-flex" }}
            aria-label="Close and cancel review"
            disabled={confirming || cancelling}
            onClick={onCancel}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="preview-body">
          {error && (
            <div className="pgroup" role="alert">
              <span className="amber" style={{ fontSize: 13 }}>
                {error}
              </span>
            </div>
          )}

          {stale && (
            <div
              className="pgroup"
              role="alert"
              style={{
                background: "rgba(224, 163, 92, 0.12)",
                padding: "10px 14px",
                borderRadius: 6,
                border: "1px solid var(--amber)",
              }}
            >
              <span className="amber" style={{ fontWeight: 500 }}>
                This proposal is stale.
              </span>
              <span className="cap" style={{ color: "var(--text-2)" }}>
                The committed portfolio has changed since this review was prepared. Your draft
                edits have been preserved — please cancel this preview and review again against
                the current portfolio.
              </span>
            </div>
          )}

          {proposal.warnings.length > 0 && (
            <div
              className="pgroup"
              role="alert"
              aria-label="Warnings"
              style={{
                background: "rgba(224, 163, 92, 0.08)",
                padding: "10px 14px",
                borderRadius: 6,
                border: "1px solid rgba(224, 163, 92, 0.3)",
              }}
            >
              <span className="lbl amber">Warnings ({proposal.warnings.length})</span>
              <ul style={{ listStyle: "none", display: "flex", flexDirection: "column", gap: 4, margin: 0 }}>
                {proposal.warnings.map((w, i) => (
                  <li key={i} className="cap" style={{ color: "var(--amber)" }}>
                    • {w}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="pgroup">
            <span className="lbl">
              Differences ({proposal.differences.length})
            </span>
            {proposal.differences.length === 0 ? (
              <p className="cap">No differences detected.</p>
            ) : (
              <ul className="diff-list" role="list">
                {proposal.differences.map((diff, index) => (
                  <li key={`${diff.path}-${index}`} className="diff-item">
                    <span style={{ color: "var(--text)" }}>{diff.description}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <footer className="preview-actions">
          <button
            type="button"
            className="btn secondary small"
            data-testid="cancel-proposal"
            disabled={confirming || cancelling}
            onClick={onCancel}
          >
            {cancelling ? "Cancelling…" : stale ? "Dismiss" : "Cancel"}
          </button>
          {!stale && (
            <button
              ref={confirmBtnRef}
              type="button"
              className="btn primary small"
              data-testid="confirm-proposal"
              disabled={confirming || cancelling}
              onClick={onConfirm}
            >
              {confirming ? "Confirming…" : "Confirm changes"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
