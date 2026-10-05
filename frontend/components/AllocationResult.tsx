import { valueLabel, weightLabel, type AllocationResult as Result } from "../lib/contracts";
import StockResult from "./StockResult";

export default function AllocationResult({ allocation }: { allocation: Result | null }) {
  if (!allocation) return null;
  return <section className="panel" aria-label="New cash analysis"><h2>New cash analysis</h2>
    <p>Fresh scan requested at {allocation.scan.scanned_at}; evidence basis: {allocation.scan.as_of}.</p>
    <p>{allocation.scan.source}</p>
    <p>Deep research: {allocation.researched.length} candidates. Research stops when no candidate could change the decision.</p>
    {allocation.scan.issues.map((issue, index) => <p className="qualification" key={index}>{issue}</p>)}
    <details><summary>Bounded opportunity screen</summary><ul>{allocation.scan.candidates.map(candidate => <li key={candidate.position.id}>
      <strong>{candidate.position.ticker} / {candidate.position.listing}</strong>: {candidate.signal} <small>{candidate.source} / {candidate.as_of}</small>
      {candidate.source_url && /^https?:\/\//.test(candidate.source_url) && <a href={candidate.source_url} target="_blank" rel="noreferrer">Screen source</a>}
    </li>)}</ul></details>
    {allocation.researched.map(chosen => <p key={chosen.position_id}>Research reason for {chosen.position_id}: {chosen.reason}</p>)}
    {allocation.stocks.map(stock => <StockResult key={stock.position_id} stock={stock} currency="USD" />)}
    {allocation.judgment && <p>Judged post-contribution exposure: {weightLabel(allocation.judgment.min_weight)} to {weightLabel(allocation.judgment.max_weight)} for {allocation.judgment.position_id}. {allocation.judgment.reason}</p>}
    {allocation.amount && <p>Approximate new-cash range: {valueLabel(allocation.amount.minimum, allocation.amount.currency)} to {valueLabel(allocation.amount.maximum, allocation.amount.currency)} for {allocation.amount.position_id}.</p>}
    {allocation.missing_inputs.length > 0 && <><h3>Decision-critical unresolved inputs</h3><ul>{allocation.missing_inputs.map(item => <li key={item}>{item}</li>)}</ul></>}
    <ul>{allocation.qualifications.map(item => <li key={item}>{item}</li>)}</ul>
  </section>;
}
