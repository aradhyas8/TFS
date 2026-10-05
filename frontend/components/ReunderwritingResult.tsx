import { actionLabel, type ReunderwritingResult as Review, type Snapshot } from "../lib/contracts";
import StockResult from "./StockResult";

export default function ReunderwritingResult({ review, positions }: { review: Review | null; positions: Snapshot["positions"] }) {
  if (!review) return null;
  return <section className="panel" aria-label="Portfolio thesis review">
    <h2>Current theses and material changes</h2>
    {review.assessments.map(row => {
      const stock = review.stocks.find(stock => stock.position_id === row.position_id);
      const prior = review.context.prior_theses.find(prior => prior.company_id === stock?.research.company_id);
      return <section key={row.position_id} aria-label={`Thesis ${row.position_id}`}>
        <h3>{positions.find(pos => pos.id === row.position_id)?.ticker || row.position_id}: {actionLabel(row.action)}</h3>
        {prior && <p>Supplied prior thesis ({prior.as_of}): {prior.thesis}</p>}
        <p>Change status: {row.status}</p><p>{row.current_thesis}</p><p>{row.change_reason}</p>
        <p>Downside: {row.downside}</p><ul>{row.what_could_change.map((item, index) => <li key={index}>{item}</li>)}</ul>
        <p>Thesis evidence: {row.evidence_ids.map(id => { const doc = stock?.research.documents.find(doc => doc.id === id);
          return doc ? <a key={id} href={doc.url} target="_blank" rel="noreferrer">{doc.title} ({doc.published_on}) </a> : <span key={id}>{id} </span>; })}</p>
        <StockResult stock={stock || null} currency={positions.find(pos => pos.id === row.position_id)?.currency || ""} />
      </section>;
    })}
    {!review.assessments.length && <p>No held US company has listing-specific research in this review. Current ETF/cash exposure is still compared; uncovered company outcomes remain unknown.</p>}
    {review.sizing && <p>Adjustment range rationale: {review.sizing.reason}</p>}
    {review.missing_inputs.length > 0 && <><h3>Unresolved adjustment inputs</h3><ul>{review.missing_inputs.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
    <ul>{review.qualifications.map((item, index) => <li key={index}>{item}</li>)}</ul>
  </section>;
}
