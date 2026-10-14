"use client";

import { AlertTriangle, Check } from "lucide-react";
import { useEffect, useRef } from "react";
import { Alert } from "../arc/alert/alert";
import { Button } from "../arc/button/button";
import { Drawer, DrawerContent, useDrawerItem } from "../arc/drawer/drawer";
import type { PortfolioProposal, Snapshot } from "../../lib/contracts";
import { ORIGIN, changeRows, isStale, unchangedGroups } from "./proposal";

export type PortfolioPreviewProps = {
  /** The proposal under review; null closes the review. */
  proposal: PortfolioProposal | null;
  /** The committed portfolio now, to name holdings and accounts and to tell whether the proposal is out of date. */
  snapshot: Snapshot | null;
  committedRevision: number | null;
  onConfirm: () => void;
  /** Cancels the proposal on the server. */
  onCancel: () => void;
  /** Closes the review and leaves the proposal waiting. */
  onClose: () => void;
  confirming?: boolean;
  cancelling?: boolean;
  error?: string | null;
  /** The backend refused to confirm it because the portfolio moved on. */
  refused?: boolean;
  /** Where focus goes on closing when the control that opened the review is gone. */
  fallbackFocus?: () => HTMLElement | null;
};

const prepared = (proposal: PortfolioProposal) =>
  proposal.base_revision != null ? `prepared from revision ${proposal.base_revision}` : "a first portfolio";

/** The review of a prepared portfolio change: what changes, as saved now and after confirming, the revision it was
 *  prepared from, and an explicit Confirm that saves a new revision. Closing leaves the proposal waiting; only Cancel
 *  proposal cancels it. A stale proposal can't be confirmed and is never moved onto the newer revision. */
export default function PortfolioPreview({ proposal, snapshot, committedRevision, onConfirm, onCancel, onClose, confirming = false, cancelling = false, error = null, refused = false, fallbackFocus }: PortfolioPreviewProps) {
  // The last proposal stays on screen while the panel slides out.
  const shown = useDrawerItem(proposal);
  const busy = confirming || cancelling;
  // What opened the review gets focus back. Its button may be disabled while the proposal is prepared, which drops
  // focus to the page, so the last control focused outside the review is remembered rather than read at opening.
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const track = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement && !event.target.closest("[role='dialog']")) opener.current = event.target;
    };
    document.addEventListener("focusin", track);
    return () => document.removeEventListener("focusin", track);
  }, []);
  const giveBack = (event: Event) => {
    event.preventDefault();
    const target = opener.current;
    (target?.isConnected && !target.matches(":disabled") ? target : fallbackFocus?.())?.focus();
  };
  if (!shown) return null;

  // The page may not have loaded the newer revision yet when the backend refuses; then it is not named.
  const behind = isStale(shown, committedRevision);
  const stale = refused || behind;
  const rows = changeRows(shown, snapshot);
  const untouched = unchangedGroups(shown);
  const changes = shown.differences.length;
  const base = shown.base_revision;
  const stayWhileBusy = (event: Event) => { if (busy) event.preventDefault(); };

  return <Drawer open={!!proposal} onOpenChange={open => { if (!open && !busy) onClose(); }}>
    <DrawerContent title="Review portfolio changes" description={`${ORIGIN[shown.origin] || "Change"} · ${prepared(shown)}`}
      className="review-sheet" onEscapeKeyDown={stayWhileBusy} onInteractOutside={stayWhileBusy} onCloseAutoFocus={giveBack}>
      <div className="review">
        <p className="review-rev">
          {refused && !behind ? "Your saved portfolio changed after this was prepared"
            : committedRevision != null ? `Your portfolio is at revision ${committedRevision}` : "No portfolio is saved yet"}
          {" · "}{changes} change{changes === 1 ? "" : "s"}
        </p>

        {stale && <Alert tone="danger" title="This proposal is out of date" id="review-stale">
          It was {prepared(shown)}{behind && committedRevision != null ? `, and your portfolio is now revision ${committedRevision}` : ", and the saved portfolio has changed since"}. It can&apos;t be
          confirmed. It won&apos;t be moved onto the newer portfolio. Cancel it, then review your changes again. Edits you haven&apos;t saved are kept.
        </Alert>}
        {error && !stale && <Alert tone="danger" title="Not confirmed">{error}</Alert>}

        <div className="review-table" role="table" aria-label="What changes">
          <div role="row" className="review-row head">
            <span role="columnheader">What changes</span><span role="columnheader">Before</span><span role="columnheader">After</span>
          </div>
          {rows.map(row => <div role="row" className="review-row" data-change={row.change} key={row.key}>
            <span role="cell" className="what"><b>{row.subject}</b><small>{row.detail}</small></span>
            <span role="cell" className="was">{row.before}</span>
            <span role="cell" className="now">{row.after}</span>
          </div>)}
        </div>

        {untouched.length > 0 && <ul className="review-same" aria-label="Left as they are">
          {untouched.map(label => <li key={label}><span>{label}</span><b>Unchanged</b></li>)}
        </ul>}

        {shown.warnings.length > 0 && <section className="review-warn" aria-labelledby="review-warn-title">
          <AlertTriangle size={17} aria-hidden="true" />
          <div>
            <h3 id="review-warn-title">Check before confirming · {shown.warnings.length}</h3>
            <ul className="review-warnings">{shown.warnings.slice(0, 3).map((warning, index) => <li key={index}>{warning}</li>)}</ul>
            {shown.warnings.length > 3 && <details className="review-more">
              <summary>{shown.warnings.length - 3} more</summary>
              <ul className="review-warnings">{shown.warnings.slice(3).map((warning, index) => <li key={index}>{warning}</li>)}</ul>
            </details>}
          </div>
        </section>}

        <p className="review-note">Values and weights aren&apos;t recalculated here. They come from the next analysis of the saved portfolio.</p>

        <footer className="review-foot">
          <p>
            {base != null ? `Confirming saves a new revision after revision ${base}, with a receipt. Analyses already stored keep revision ${base}.` : "Confirming saves your first portfolio revision, with a receipt."}
            {" "}It updates your records only. It does not place a trade.
          </p>
          <div className="review-actions">
            <Button variant="primary" data-testid="confirm-proposal" onClick={onConfirm} loading={confirming}
              disabled={stale || cancelling} aria-describedby={stale ? "review-stale" : undefined}>
              {!confirming && <Check size={16} aria-hidden="true" />}{confirming ? "Confirming…" : "Confirm changes"}
            </Button>
            <Button variant="ghost" data-testid="cancel-proposal" onClick={onCancel} loading={cancelling} disabled={confirming}>
              {cancelling ? "Cancelling…" : "Cancel proposal"}
            </Button>
          </div>
        </footer>
      </div>
    </DrawerContent>
  </Drawer>;
}
