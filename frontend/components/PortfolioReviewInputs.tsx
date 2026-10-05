import type { PortfolioReviewInput, Snapshot } from "../lib/contracts";

export default function PortfolioReviewInputs({ enabled, onEnabled, value, onChange, snapshot, busy }: {
  enabled: boolean; onEnabled: (enabled: boolean) => void; value: PortfolioReviewInput;
  onChange: (value: PortfolioReviewInput) => void; snapshot: Snapshot; busy: boolean;
}) {
  const companies = [...new Map(snapshot.positions.filter(row => row.kind === "stock" && row.company_id)
    .map(row => [row.company_id!, row])).values()];
  return <section className="panel" aria-label="Portfolio re-underwriting inputs">
    <h2>Re-underwrite the whole portfolio</h2>
    <label><input type="checkbox" checked={enabled} disabled={busy} onChange={event => onEnabled(event.target.checked)} /> Review holdings and rebalance</label>
    {enabled && <fieldset disabled={busy}>
      <p className="muted small">Review current evidence across your holdings and accounts. A falling price is context; it does not establish thesis failure or justify adding. Targets come only from your optional baseline above.</p>
      <label>Portfolio review risk and withdrawal context<textarea maxLength={1000} value={value.risk_context || ""}
        onChange={event => onChange({ ...value, risk_context: event.target.value || null })} /></label>
      <p className="muted small">Supply a dated prior thesis to compare what changed. Without one, the current thesis is reviewed and its change status remains unknown.</p>
      {companies.map(company => {
        const prior = value.prior_theses.find(row => row.company_id === company.company_id);
        function patch(change: Partial<PortfolioReviewInput["prior_theses"][number]>) {
          const next = { company_id: company.company_id!, as_of: "", thesis: "", ...prior, ...change };
          onChange({ ...value, prior_theses: [...value.prior_theses.filter(row => row.company_id !== company.company_id), ...(next.thesis || next.as_of ? [next] : [])] });
        }
        return <div key={company.company_id} className="grid two">
          <label>Prior thesis for {company.company_id}<textarea maxLength={1000} required={!!prior?.as_of} value={prior?.thesis || ""} onChange={event => patch({ thesis: event.target.value })} /></label>
          <label>Prior thesis date for {company.company_id}<input type="date" max={snapshot.as_of || undefined} required={!!prior?.thesis} value={prior?.as_of || ""} onChange={event => patch({ as_of: event.target.value })} /></label>
        </div>;
      })}
    </fieldset>}
  </section>;
}
