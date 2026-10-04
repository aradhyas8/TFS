"use client";

import type { Dispatch, SetStateAction } from "react";
import type { Baseline, PortfolioSettings, ProposedChanges, Snapshot } from "../lib/contracts";

function DecimalInput({ label, value, onChange, fraction = false, signed = false, required = false }: {
  label: string; value?: string | null; onChange: (value: string) => void;
  fraction?: boolean; signed?: boolean; required?: boolean;
}) {
  return <label className="field"><span>{label}</span><input type="number" step="any"
    min={signed ? undefined : "0"} max={fraction ? "1" : undefined} required={required}
    value={value ?? ""} onChange={event => onChange(event.target.value)} /></label>;
}

export default function GuardrailInputs({ settings, setSettings, changes, setChanges, snapshot, busy }: {
  settings: PortfolioSettings; setSettings: Dispatch<SetStateAction<PortfolioSettings>>;
  changes: ProposedChanges; setChanges: Dispatch<SetStateAction<ProposedChanges>>;
  snapshot: Snapshot; busy: boolean;
}) {
  function baselineChange(category: keyof Baseline, value: string) {
    setSettings(current => {
      const baseline = { ...current.baseline, [category]: value || undefined };
      return { ...current, baseline: Object.values(baseline).some(item => item !== undefined) ? baseline : undefined };
    });
  }
  const cashRows = snapshot.positions.filter(row => row.kind === "cash");
  const securityRows = snapshot.positions.filter(row => row.kind !== "cash");
  function positionLabel(id: string) {
    const position = snapshot.positions.find(row => row.id === id);
    return `${position?.ticker || "Cash"} / ${snapshot.accounts.find(account => account.id === position?.account_id)?.name || "Unnamed account"} / ${position?.currency}`;
  }
  return <section className="panel" aria-labelledby="guardrail-title">
    <h2 id="guardrail-title">Your explicit portfolio settings</h2>
    <p className="muted small">Optional. Leave unknown values blank. Enter weights as fractions of the whole portfolio between zero and one. These inputs do not create a risk profile.</p>
    <fieldset disabled={busy} className="editor-fields">
      <div className="grid two">
        <DecimalInput label="Single-company cap (fraction)" fraction value={settings.single_company_cap}
          onChange={single_company_cap => setSettings(current => ({ ...current, single_company_cap: single_company_cap || undefined }))} />
        <DecimalInput label="Active budget (fraction)" fraction value={settings.active_budget}
          onChange={active_budget => setSettings(current => ({ ...current, active_budget: active_budget || undefined }))} />
        <label className="field"><span>Company cap policy for indirect exposure</span><select value={settings.indirect_cap_policy ?? ""}
          onChange={event => setSettings(current => ({ ...current, indirect_cap_policy: event.target.value as PortfolioSettings["indirect_cap_policy"] || undefined }))}>
          <option value="">Unknown / not supplied</option><option value="direct_only">Direct exposure only</option>
          <option value="include_known_indirect">Include known indirect exposure</option>
        </select></label>
        <label className="field"><span>Cash above baseline is a deliberate tilt</span><select
          value={settings.cash_is_deliberate_tilt === undefined ? "" : String(settings.cash_is_deliberate_tilt)}
          onChange={event => setSettings(current => ({ ...current, cash_is_deliberate_tilt: event.target.value === "" ? undefined : event.target.value === "true" }))}>
          <option value="">Unknown / not supplied</option><option value="true">Yes; count excess cash</option><option value="false">No deliberate cash tilt</option>
        </select></label>
      </div>
      <h3>Baseline allocation, if supplied</h3>
      <p className="muted small">Partial baselines stay partial. Only supplied targets are compared. Individual stocks and sector/theme ETFs count toward the active budget. Deliberate excess cash also needs a cash baseline.</p>
      <div className="grid two">{([
        ["stocks", "Stocks baseline (fraction)"], ["diversified_etfs", "Diversified ETFs baseline (fraction)"],
        ["sector_theme_etfs", "Sector/theme ETFs baseline (fraction)"], ["cash", "Cash baseline (fraction)"],
      ] as const).map(([category, label]) => <DecimalInput key={category} label={label} fraction
        value={settings.baseline?.[category]} onChange={value => baselineChange(category, value)} />)}</div>
      <details><summary>Preview explicit proposed changes</summary>
        <p className="muted small">A hypothetical check using the same dated marks and FX. No orders are placed. Positive shares use cash; negative shares return proceeds to cash. Funding must be in the same account and currency. New cash amounts use the selected cash balance’s currency. Costs and taxes remain unknown.</p>
        <button type="button" className="text-button" disabled={!cashRows.length} onClick={() => setChanges(current => ({
          ...current, new_cash: [...current.new_cash, { cash_position_id: cashRows[0].id, amount: "" }],
        }))}>+ Add proposed new cash</button>
        {changes.new_cash.map((row, index) => <div className="grid three" key={index}>
          <label className="field"><span>New cash destination</span><select value={row.cash_position_id} required
            onChange={event => setChanges(current => ({ ...current, new_cash: current.new_cash.map((item, i) => i === index ? { ...item, cash_position_id: event.target.value } : item) }))}>
            {cashRows.map(cash => <option key={cash.id} value={cash.id}>{positionLabel(cash.id)}</option>)}
          </select></label>
          <DecimalInput label="New cash amount" required value={row.amount} onChange={amount => setChanges(current => ({
            ...current, new_cash: current.new_cash.map((item, i) => i === index ? { ...item, amount } : item),
          }))} />
          <button type="button" className="text-button" onClick={() => setChanges(current => ({ ...current, new_cash: current.new_cash.filter((_, i) => i !== index) }))}>Remove new cash {index + 1}</button>
        </div>)}
        <button type="button" className="text-button" disabled={!cashRows.length || !securityRows.length} onClick={() => setChanges(current => ({
          ...current, trades: [...current.trades, { position_id: securityRows[0].id, shares_change: "", cash_position_id: cashRows[0].id }],
        }))}>+ Add proposed share change</button>
        {changes.trades.map((row, index) => <fieldset className="position-editor" key={index}><legend>Proposed share change {index + 1}</legend><div className="grid three">
          <label className="field"><span>Proposed security</span><select value={row.position_id} required
            onChange={event => setChanges(current => ({ ...current, trades: current.trades.map((item, i) => i === index ? { ...item, position_id: event.target.value } : item) }))}>
            {securityRows.map(position => <option key={position.id} value={position.id}>{positionLabel(position.id)}</option>)}
          </select></label>
          <DecimalInput label="Share change" signed required value={row.shares_change} onChange={shares_change => setChanges(current => ({
            ...current, trades: current.trades.map((item, i) => i === index ? { ...item, shares_change } : item),
          }))} />
          <label className="field"><span>Funding cash balance</span><select value={row.cash_position_id} required
            onChange={event => setChanges(current => ({ ...current, trades: current.trades.map((item, i) => i === index ? { ...item, cash_position_id: event.target.value } : item) }))}>
            {cashRows.map(cash => <option key={cash.id} value={cash.id}>{positionLabel(cash.id)}</option>)}
          </select></label>
        </div><button type="button" className="text-button" onClick={() => setChanges(current => ({ ...current, trades: current.trades.filter((_, i) => i !== index) }))}>Remove share change {index + 1}</button></fieldset>)}
      </details>
    </fieldset>
  </section>;
}
