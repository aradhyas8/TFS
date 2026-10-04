import type { Dispatch, SetStateAction } from "react";
import { post, type FX, type Position, type Snapshot } from "../lib/contracts";

type Props = { snapshot: Snapshot; setSnapshot: Dispatch<SetStateAction<Snapshot>>; busy: boolean;
  onError: (message: string) => void; importing: boolean; setImporting: (value: boolean) => void };

function Field({ label, value, onChange, type = "text", required = false, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; type?: string; required?: boolean; placeholder?: string;
}) {
  return <label className="field"><span>{label}</span><input type={type} value={value} required={required}
    placeholder={placeholder} onChange={event => onChange(event.target.value)} /></label>;
}

export default function PortfolioEditor({ snapshot, setSnapshot, busy, onError, importing, setImporting }: Props) {
  const positionChange = (index: number, patch: Partial<Position>) => setSnapshot(current => ({
    ...current, positions: current.positions.map((position, i) => i === index ? { ...position, ...patch } : position),
  }));
  const fxChange = (index: number, patch: Partial<FX>) => setSnapshot(current => ({
    ...current, fx: current.fx.map((fx, i) => i === index ? { ...fx, ...patch } : fx),
  }));

  async function importCSV(file: File) {
    if (file.size > 1_000_000) { onError("CSV must be no larger than 1 MB."); return; }
    setImporting(true); onError("");
    try {
      const loaded = await post<Snapshot>("/api/portfolio/csv", { csv: await file.text(),
        as_of: snapshot.as_of, reporting_currency: snapshot.reporting_currency });
      setSnapshot(loaded);
    } catch (error) { onError(error instanceof Error ? error.message : "CSV import failed."); }
    finally { setImporting(false); }
  }

  return <section className="panel" aria-labelledby="snapshot-title">
    <div className="section-heading"><div><p className="eyebrow">01 / PORTFOLIO CONTEXT</p><h2 id="snapshot-title">Your dated snapshot</h2></div>
      <label className="button secondary upload">{importing ? "Loading…" : "Load CSV"}<input aria-label="Load portfolio CSV"
        type="file" accept=".csv,text/csv" disabled={busy} onChange={event => {
          const file = event.target.files?.[0]; if (file) void importCSV(file); event.target.value = "";
        }} /></label></div>
    <p className="muted">Include every account, holding and cash balance. Enter a broker-display mark when provider coverage is unavailable. Marks are manual and indicative. Shares must reflect splits on your snapshot date.</p>
    <p className="small"><a href="/api/portfolio/template" download>Download CSV template</a></p>
    <fieldset disabled={busy} className="editor-fields">
      <div className="grid two"><Field label="As-of date" type="date" required value={snapshot.as_of}
        onChange={as_of => setSnapshot(current => ({ ...current, as_of }))} />
        <Field label="Reporting currency" required placeholder="CAD" value={snapshot.reporting_currency}
          onChange={reporting_currency => setSnapshot(current => ({ ...current, reporting_currency: reporting_currency.toUpperCase() }))} /></div>
      <div className="subheading"><h3>Accounts</h3><button type="button" className="text-button" onClick={() => setSnapshot(current => ({
        ...current, accounts: [...current.accounts, { id: crypto.randomUUID(), name: "" }],
      }))}>+ Add account</button></div>
      {snapshot.accounts.map((account, index) => <div className="account-row" key={account.id}>
        <Field label={`Account ${index + 1} name`} required value={account.name} onChange={name => setSnapshot(current => ({
          ...current, accounts: current.accounts.map((item, i) => i === index ? { ...item, name } : item),
        }))} />
        <button type="button" className="text-button" disabled={snapshot.accounts.length === 1 || snapshot.positions.some(p => p.account_id === account.id)}
          onClick={() => setSnapshot(current => ({ ...current, accounts: current.accounts.filter(item => item.id !== account.id) }))}>Remove account {index + 1}</button>
      </div>)}
      <div className="subheading"><h3>Holdings &amp; cash</h3><button type="button" className="text-button" onClick={() => setSnapshot(current => ({
        ...current, positions: [...current.positions, { id: crypto.randomUUID(), account_id: current.accounts[0].id,
          kind: "stock", currency: current.reporting_currency, shares: "", mark: null }],
      }))}>+ Add position</button></div>
      {snapshot.positions.length === 0 && <p className="muted empty-small">Add a holding or cash balance, or load a CSV snapshot.</p>}
      {snapshot.positions.map((position, index) => <fieldset className="position-editor" key={position.id}>
        <legend>Position {index + 1}</legend>
        <div className="grid three">
          <label className="field"><span>Account</span><select value={position.account_id} onChange={event => positionChange(index, { account_id: event.target.value })}>
            {snapshot.accounts.map(account => <option key={account.id} value={account.id}>{account.name || "Unnamed account"}</option>)}
          </select></label>
          <label className="field"><span>Kind</span><select value={position.kind} onChange={event => {
            const kind = event.target.value as Position["kind"];
            if (kind === position.kind) return;
            if (kind !== "cash" && position.kind !== "cash") {
              positionChange(index, { kind, company_id: null, company_name: null, etf_role: null });
            } else {
              positionChange(index, { kind, shares: kind === "cash" ? null : "", cash: kind === "cash" ? "" : null,
                mark: null, ticker: null, listing: null, company_id: null, company_name: null, etf_role: null });
            }
          }}><option value="stock">Company stock</option><option value="etf">ETF</option><option value="cash">Cash</option></select></label>
          <Field label="Quote / cash currency" required value={position.currency} onChange={currency => positionChange(index, { currency: currency.toUpperCase() })} />
          {position.kind === "cash" ? <Field label="Cash balance" required value={position.cash || ""} onChange={cash => positionChange(index, { cash })} /> : <>
            {position.kind === "etf" && <label className="field"><span>ETF classification for active budget</span><select value={position.etf_role ?? ""}
              onChange={event => positionChange(index, { etf_role: event.target.value as Position["etf_role"] || null })}>
              <option value="">Unknown / not supplied</option><option value="diversified">Diversified ETF</option>
              <option value="sector_theme">Sector or theme ETF</option>
            </select></label>}
            <Field label="Ticker" value={position.ticker || ""} onChange={ticker => positionChange(index, { ticker: ticker || null })} />
            <Field label="Listing / exchange" placeholder="XNAS" value={position.listing || ""} onChange={listing => positionChange(index, { listing: listing || null })} />
            <Field label="Shares" required value={position.shares || ""} onChange={shares => positionChange(index, { shares })} />
            {position.kind === "stock" && <><Field label="Company ID (shared across listings)" value={position.company_id || ""}
              onChange={company_id => positionChange(index, { company_id: company_id || null })} />
              <Field label="Company name" value={position.company_name || ""} onChange={company_name => positionChange(index, { company_name: company_name || null })} /></>}
            <Field label="Supplied mark" value={position.mark?.value || ""} onChange={value => positionChange(index, {
              mark: value ? { ...position.mark, value, as_of: position.mark?.as_of || snapshot.as_of, source: position.mark?.source || "User-entered broker-display mark" } : null,
            })} />
            {position.mark && <><Field label="Mark date" type="date" required value={position.mark.as_of}
              onChange={as_of => positionChange(index, { mark: { ...position.mark!, as_of } })} />
              <Field label="Mark source label" required value={position.mark.source}
                onChange={source => positionChange(index, { mark: { ...position.mark!, source } })} />
              <Field label="Mark capture time (optional, with timezone)" placeholder="2026-09-30T20:00:00Z" value={position.mark.captured_at || ""}
                onChange={captured_at => positionChange(index, { mark: { ...position.mark!, captured_at: captured_at || null } })} />
              <label className="field"><span>Price basis</span><select value={position.mark.basis || "unadjusted"}
                onChange={event => positionChange(index, { mark: { ...position.mark!, basis: event.target.value as NonNullable<Position["mark"]>["basis"] } })}>
                <option value="unadjusted">Unadjusted broker mark</option><option value="split_adjusted">Split-adjusted history (unusable)</option>
                <option value="total_return_adjusted">Dividend-adjusted history (unusable)</option><option value="unknown">Unknown basis (unusable)</option>
              </select></label></>}
          </>}
        </div>
        <button type="button" className="text-button remove" onClick={() => setSnapshot(current => ({
          ...current, positions: current.positions.filter(item => item.id !== position.id),
        }))}>Remove position {index + 1}</button>
      </fieldset>)}
      <div className="subheading"><h3>Dated FX</h3><button type="button" className="text-button" onClick={() => setSnapshot(current => ({
        ...current, fx: [...current.fx, { from_currency: "USD", to_currency: current.reporting_currency,
          rate: "", as_of: current.as_of, source: "User-supplied FX" }],
      }))}>+ Add FX rate</button></div>
      <p className="muted small">Enter reporting-currency units per one local-currency unit. FX is indicative, not an execution quote. The backend can fetch dated Bank of Canada CAD rates when enabled. Cross rates are never inferred.</p>
      {snapshot.fx.map((fx, index) => <fieldset className="position-editor" key={index}><legend>FX rate {index + 1}</legend><div className="grid three">
        <Field label="From currency" required value={fx.from_currency} onChange={from_currency => fxChange(index, { from_currency: from_currency.toUpperCase() })} />
        <Field label="To currency" required value={fx.to_currency} onChange={to_currency => fxChange(index, { to_currency: to_currency.toUpperCase() })} />
        <Field label="FX rate" required value={fx.rate} onChange={rate => fxChange(index, { rate })} />
        <Field label="FX date" required type="date" value={fx.as_of} onChange={as_of => fxChange(index, { as_of })} />
        <Field label="FX source label" required value={fx.source} onChange={source => fxChange(index, { source })} />
        <Field label="FX capture time (optional, with timezone)" placeholder="2026-09-30T21:00:00Z" value={fx.captured_at || ""}
          onChange={captured_at => fxChange(index, { captured_at: captured_at || null })} />
      </div><button type="button" className="text-button remove" onClick={() => setSnapshot(current => ({
        ...current, fx: current.fx.filter((_, i) => i !== index),
      }))}>Remove FX rate {index + 1}</button></fieldset>)}
    </fieldset>
  </section>;
}
