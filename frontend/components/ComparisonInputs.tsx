import { portfolioReviewComparison, type ComparisonInput, type FundFacts, type KnownEffects, type Snapshot } from "../lib/contracts";

export default function ComparisonInputs({ value, onChange, snapshot, busy, stockId, portfolioReview = false, themeDiscovery = false }: {
  value: ComparisonInput | null; onChange: (value: ComparisonInput | null) => void; snapshot: Snapshot; busy: boolean; stockId?: string; portfolioReview?: boolean; themeDiscovery?: boolean;
}) {
  const empty: ComparisonInput = portfolioReview ? portfolioReviewComparison(snapshot) : { scope_position_ids: portfolioReview ? snapshot.positions.map(row => row.id) : [], alternatives: [{ id: "keep", kind: "no_action", position_id: null }], fund_facts: [], effects: [] };
  function destination(id: string, kind: "stock" | "etf" | "cash" | "short_bill", position: string) {
    if (!value) return;
    onChange({ ...value, alternatives: [...value.alternatives.filter(row => row.id !== id),
      ...(position ? [{ id, kind, position_id: position }] : [])] });
  }
  function fact(position: string, patch: Partial<FundFacts>) {
    if (!value) return;
    const previous = value.fund_facts.find(row => row.position_id === position);
    onChange({ ...value, fund_facts: [...value.fund_facts.filter(row => row.position_id !== position),
      { position_id: position, exposure: "", as_of: snapshot.as_of, source: "User supplied fund facts",
        source_url: null, annual_cost: null, income_yield: null, ...previous, ...patch }] });
  }
  function effect(id: string, patch: Partial<KnownEffects>) {
    if (!value) return;
    onChange({ ...value, effects: [...value.effects.filter(row => row.alternative_id !== id),
      { alternative_id: id, as_of: snapshot.as_of, source: "User supplied effects", transaction_cost: null,
        terminal_tax: null, ...value.effects.find(row => row.alternative_id === id), ...patch }] });
  }
  const cash = value?.alternatives.find(row => row.id === "cash");
  const relevant = new Set([...(value?.scope_position_ids || []), ...(value?.alternatives.map(row => row.position_id) || [])]);
  return <section className="panel comparison-inputs" aria-label="Comparison inputs">
    <h2>Five-year comparison</h2>
    <p className="muted small">Compare selected alternatives using the value of actual holdings and cash. This is a comparison basis, not an allocation instruction. Add a candidate fund to the snapshot with zero shares if needed.</p>
    <label><input type="checkbox" checked={value !== null} disabled={busy || themeDiscovery} onChange={event => onChange(event.target.checked ? empty : null)} /> Include conditional comparison</label>
    {value && <fieldset disabled={busy}>
      <legend>Current holdings and cash to compare</legend>
      {value.scope_position_ids.length === 0 && <p className="qualification">Select at least one current holding or cash balance for the comparison basis.</p>}
      {value.alternatives.length === 0 && <p className="qualification">Select at least one comparison alternative.</p>}
      {snapshot.positions.map(row => <label key={row.id}><input type="checkbox" disabled={portfolioReview || themeDiscovery} aria-label={`Comparison scope ${row.id}`}
        checked={value.scope_position_ids.includes(row.id)} onChange={event => onChange({ ...value,
          scope_position_ids: event.target.checked ? [...value.scope_position_ids, row.id] : value.scope_position_ids.filter(id => id !== row.id) })} /> {row.ticker || "Cash"} / {row.id} / {row.currency}</label>)}
      {(portfolioReview || themeDiscovery) && value.alternatives.filter(row => row.kind === "stock").map(row => <p key={row.id} className="muted small">Company alternative: {snapshot.positions.find(pos => pos.id === row.position_id)?.ticker} / {row.position_id}</p>)}
      {stockId && <label><input type="checkbox" checked={value.alternatives.some(row => row.kind === "stock")}
        onChange={event => destination("company", "stock", event.target.checked ? stockId : "")} /> Compare the researched stock</label>}
      <div className="form-grid">
        <label>Diversified ETF alternative<select disabled={themeDiscovery} value={value.alternatives.find(row => row.id === "fund")?.position_id || ""}
          onChange={event => destination("fund", "etf", event.target.value)}><option value="">Not selected</option>
          {snapshot.positions.filter(row => row.kind === "etf" && row.etf_role === "diversified").map(row => <option key={row.id} value={row.id}>{row.ticker} / {row.id}</option>)}</select></label>
        <label>Cash or short-bill currency<select disabled={themeDiscovery} value={cash?.position_id || ""} onChange={event => destination("cash", cash?.kind === "short_bill" ? "short_bill" : "cash", event.target.value)}>
          <option value="">Not selected</option>{snapshot.positions.filter(row => row.kind === "cash").map(row => <option key={row.id} value={row.id}>{row.id} / {row.currency}</option>)}</select></label>
        {cash && <label>Cash alternative type<select disabled={themeDiscovery} value={cash.kind} onChange={event => destination("cash", event.target.value as "cash" | "short_bill", cash.position_id || "")}>
          <option value="cash">Cash</option><option value="short_bill">Short government bills</option></select></label>}
      </div>
      <label><input type="checkbox" disabled={portfolioReview || themeDiscovery} checked={value.alternatives.some(row => row.kind === "no_action")}
        onChange={event => onChange({ ...value, alternatives: [...value.alternatives.filter(row => row.kind !== "no_action"),
          ...(event.target.checked ? [{ id: "keep", kind: "no_action" as const, position_id: null }] : [])] })} /> Compare no action: retain selected holdings and cash</label>
      {snapshot.positions.filter(row => row.kind === "etf" && relevant.has(row.id)).map(row => {
        const facts = value.fund_facts.find(f => f.position_id === row.id);
        return <fieldset key={row.id}><legend>Fund facts: {row.ticker} / {row.id}</legend>
          <p className="muted small">Supply available facts and their source. Blank costs or income remain unknown; zero means explicitly known zero.</p>
          <div className="form-grid">
            <label>Portfolio exposure for {row.id}<input required={!!facts} value={facts?.exposure || ""} onChange={event => fact(row.id, { exposure: event.target.value })} /></label>
            <label>Fund facts date for {row.id}<input type="date" value={facts?.as_of || snapshot.as_of} onChange={event => fact(row.id, { as_of: event.target.value })} /></label>
            <label>Fund source for {row.id}<input value={facts?.source || "User supplied fund facts"} onChange={event => fact(row.id, { source: event.target.value })} /></label>
            <label>Fund source link for {row.id}<input type="url" value={facts?.source_url || ""} onChange={event => fact(row.id, { source_url: event.target.value || null })} /></label>
            <label>Annual fund cost (fraction) for {row.id}<input type="number" min="0" max="1" step="any" value={facts?.annual_cost ?? ""} onChange={event => fact(row.id, { annual_cost: event.target.value || null })} /></label>
            <label>Income yield (fraction) for {row.id}<input type="number" min="0" max="1" step="any" value={facts?.income_yield ?? ""} onChange={event => fact(row.id, { income_yield: event.target.value || null })} /></label>
          </div>
          {facts && <button className="text-button" type="button" onClick={() => onChange({ ...value, fund_facts: value.fund_facts.filter(f => f.position_id !== row.id) })}>Clear fund facts for {row.id}</button>}
        </fieldset>;
      })}
      <details><summary>Known transaction costs and tax effects</summary>
        <p className="muted small">Optional reporting-currency amounts: a starting transaction cost and terminal tax effect. Supply only supported effects; blanks stay unquantified. An account name does not establish personal tax treatment.</p>
        {value.alternatives.map(alt => { const known = value.effects.find(row => row.alternative_id === alt.id);
          return <fieldset key={alt.id}><legend>{alt.kind.replaceAll("_", " ")}</legend><div className="form-grid">
            <label>Transaction cost for {alt.id}<input type="number" min="0" step="any" value={known?.transaction_cost ?? ""} onChange={event => effect(alt.id, { transaction_cost: event.target.value || null })} /></label>
            <label>Terminal tax for {alt.id}<input type="number" min="0" step="any" value={known?.terminal_tax ?? ""} onChange={event => effect(alt.id, { terminal_tax: event.target.value || null })} /></label>
            <label>Effects source for {alt.id}<input value={known?.source || "User supplied effects"} onChange={event => effect(alt.id, { source: event.target.value })} /></label>
            <label>Effects date for {alt.id}<input type="date" value={known?.as_of || snapshot.as_of} onChange={event => effect(alt.id, { as_of: event.target.value })} /></label>
          </div></fieldset>;
        })}
      </details>
    </fieldset>}
  </section>;
}
