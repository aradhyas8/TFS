import { weightLabel, type ThemeResult as Theme } from "../lib/contracts";
import StockResult from "./StockResult";

export default function ThemeResult({ theme, currency }: { theme: Theme | null; currency: string }) {
  if (!theme) return null;
  return <section className="panel" aria-label="Theme discovery">
    <h2>Theme discovery: {theme.context.name || "Clarify theme"}</h2>
    <p>{theme.status === "completed" ? "Completed" : "Awaiting agreement"}</p>
    <p>Economic mechanism: {theme.context.mechanism || "Unknown"}</p>
    <p>Agreed shortlist: {theme.context.shortlist.join(", ") || "Unknown"}. Maximum candidates: {theme.context.max_candidates}.</p>
    <p>Research calls used: {theme.tool_calls_used} / {theme.context.max_tool_calls}</p>
    {theme.tests.map(test => <div key={test.position_id}><h3>{test.position_id}: {test.conclusion}</h3><p>{test.explanation}</p><p>Evidence references: {test.evidence_ids.join(", ") || "Unknown"}</p></div>)}
    {theme.stocks.map(stock => <StockResult key={stock.position_id} stock={stock} currency={currency} />)}
    {theme.sizing && <p>Judged exposure range: {weightLabel(theme.sizing.min_weight)} to {weightLabel(theme.sizing.max_weight)}. {theme.sizing.reason}</p>}
    {theme.missing_inputs.length > 0 && <ul>{theme.missing_inputs.map(text => <li key={text}>{text}</li>)}</ul>}
    <ul>{theme.qualifications.map(text => <li key={text}>{text}</li>)}</ul>
  </section>;
}
