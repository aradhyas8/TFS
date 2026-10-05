import type { NewCashInput, Snapshot } from "../lib/contracts";

export default function NewCashInputs({ enabled, onEnabled, value, onChange, snapshot, busy }: {
  enabled: boolean; onEnabled: (value: boolean) => void; value: NewCashInput;
  onChange: (value: NewCashInput) => void; snapshot: Snapshot; busy: boolean;
}) {
  const update = (next: Partial<NewCashInput>) => onChange({ ...value, ...next });
  return <section className="panel" aria-label="New cash inputs"><h2>Decide what to do with new cash</h2>
    <label><input type="checkbox" checked={enabled} disabled={busy} onChange={event => onEnabled(event.target.checked)} />Analyze new cash</label>
    {enabled && <>
      <p className="muted small">Enter cash that is additional to your snapshot. Choose its account and currency, then confirm both. The analyst will compare focused opportunities with a diversified fund, cash and no action.</p>
      <label>New cash amount<input type="number" min="0" step="0.01" value={value.amount || ""} disabled={busy} onChange={event => update({ amount: event.target.value || null, confirmed: false })} /></label>
      <label>New cash account and currency<select value={value.cash_position_id || ""} disabled={busy} onChange={event => update({ cash_position_id: event.target.value || null, confirmed: false })}>
        <option value="">Confirm the destination cash balance</option>{snapshot.positions.filter(row => row.kind === "cash").map(row => <option key={row.id} value={row.id}>{snapshot.accounts.find(account => account.id === row.account_id)?.name} / {row.currency} / {row.id}</option>)}
      </select></label>
      <label><input type="checkbox" checked={value.confirmed} disabled={busy || !value.amount || !value.cash_position_id} onChange={event => update({ confirmed: event.target.checked })} />I confirm this amount is new cash and this is its intended account</label>
      <label>Loss tolerance and withdrawal needs<textarea maxLength={1000} disabled={busy} value={value.risk_context || ""} onChange={event => update({ risk_context: event.target.value || null })} placeholder="Describe losses you can tolerate and any planned withdrawals." /></label>
      <p className="muted small">Missing decision-critical inputs lead to conditional direction. Account type does not establish tax effects or contribution room. You place any orders yourself.</p>
    </>}
  </section>;
}
