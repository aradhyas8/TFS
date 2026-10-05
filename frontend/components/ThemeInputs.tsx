import { isThemeCandidate, type Snapshot, type ThemeInput } from "../lib/contracts";

export default function ThemeInputs({ enabled, onEnabled, value, onChange, snapshot, busy }: {
  enabled: boolean; onEnabled: (enabled: boolean) => void; value: ThemeInput; onChange: (value: ThemeInput) => void; snapshot: Snapshot; busy: boolean;
}) {
  const candidates = snapshot.positions.filter(isThemeCandidate);
  function change(patch: Partial<ThemeInput>) { onChange({ ...value, ...patch, confirmed: false }); }
  const ready = !!value.name?.trim() && !!value.mechanism?.trim() && value.shortlist.length > 0 && value.shortlist.length <= value.max_candidates;
  return <section className="panel" aria-label="Theme question inputs">
    <label><input type="checkbox" checked={enabled} disabled={busy} onChange={event => onEnabled(event.target.checked)} />Explore a theme</label>
    {enabled && <><h2>Agree a bounded theme investigation</h2>
      <label>Theme name<input value={value.name || ""} maxLength={1000} disabled={busy} onChange={event => change({ name: event.target.value || null })} /></label>
      <label>Economic mechanism<textarea aria-label="Economic mechanism" value={value.mechanism || ""} maxLength={1000} disabled={busy} onChange={event => change({ mechanism: event.target.value || null })} /></label>
      <p className="muted small">Name how the theme changes revenue, margins, reinvestment or fund exposure, and what evidence could disprove it. Add candidates with zero shares to your portfolio input if needed.</p>
      <label>Theme loss tolerance and withdrawal needs<textarea aria-label="Theme loss tolerance and withdrawal needs" value={value.risk_context || ""} maxLength={1000} disabled={busy} onChange={event => change({ risk_context: event.target.value || null })} /></label>
      <fieldset disabled={busy}><legend>Agreed shortlist</legend>{candidates.map(row => <label key={row.id}><input type="checkbox" aria-label={`Shortlist ${row.ticker} / ${row.listing} / ${row.id}`} checked={value.shortlist.includes(row.id)} disabled={!value.shortlist.includes(row.id) && value.shortlist.length >= value.max_candidates} onChange={event => change({ shortlist: event.target.checked ? [...value.shortlist, row.id] : value.shortlist.filter(id => id !== row.id) })} />{row.ticker} / {row.listing} / {row.id}</label>)}</fieldset>
      <label>Maximum candidates<input type="number" min={1} max={4} value={value.max_candidates} disabled={busy} onChange={event => { const bound = Math.max(1, Math.min(4, Number(event.target.value) || 1)); change({ max_candidates: bound, shortlist: value.shortlist.slice(0, bound) }); }} /></label>
      <label>Maximum research and calculation calls<input type="number" min={1} max={24} value={value.max_tool_calls} disabled={busy} onChange={event => change({ max_tool_calls: Math.max(1, Math.min(24, Number(event.target.value) || 1)) })} /></label>
      <p className="muted small">This limit covers portfolio review, candidate evidence, mechanism tests, cases and comparison. Research stops at the comparison. Canadian company evidence awaits the later extension.</p>
      <label><input type="checkbox" checked={value.confirmed} disabled={busy || !ready} onChange={event => onChange({ ...value, confirmed: event.target.checked })} />I agree to this mechanism, shortlist and effort bound</label>
      {!value.confirmed && <p>Submit to clarify the agreement before candidate research.</p>}
    </>}
  </section>;
}
