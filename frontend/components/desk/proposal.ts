import type { Account, BenchmarkSetting, DifferenceItem, FX, PortfolioProposal, PortfolioSettings, Position, Snapshot, UnresolvedHolding } from "../../lib/contracts";
import { fullDate, money } from "./format";
import { ROLE, SETTINGS } from "./portfolio";

/** One line of a proposal review: what changes, as it is saved now and as it would be saved after confirming. */
export type ChangeRow = { key: string; subject: string; detail: string; before: string; after: string; change: "added" | "removed" | "changed" };

export const ORIGIN: Record<PortfolioProposal["origin"], string> = {
  chat: "From a conversation", manual: "Your edits", import: "Holdings import", identify: "Identity correction",
};

/** Pending, but prepared from a revision that is no longer the committed one, or already marked stale. The backend
 *  refuses to confirm it; it is never rebased. Before the committed revision has loaded, nothing is called stale. */
export const isStale = (proposal: Pick<PortfolioProposal, "status" | "base_revision">, revision: number | null) =>
  proposal.status === "stale" || (proposal.status === "pending" && revision != null && (proposal.base_revision ?? null) !== revision);

/** Whether a difference adds, removes or changes something, from the suffix of its kind. */
const changeOf = (kind: string): ChangeRow["change"] => (kind.endsWith("_added") ? "added" : kind.endsWith("_removed") ? "removed" : "changed");
const known = (value: unknown) =>
  value == null || (Array.isArray(value) && !value.length) ? "Not known" : Array.isArray(value) ? value.join(", ") : String(value);
const capital = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const shares = (value: string | null | undefined) => (value == null ? "Unknown" : `${value} shares`);
const name = (row: Position) => (row.kind === "cash" ? `Cash ${row.currency}` : row.ticker || row.company_name || row.id);
const rate = (fx: FX | null | undefined) => (fx ? `${fx.rate} (${fullDate(fx.as_of)})` : "None");

/** A holding's fields in the order a reader checks them: what it is, where it is held, then how much. */
const FIELDS: [keyof Position, string, (row: Position) => string][] = [
  ["ticker", "ticker", row => row.ticker || "None"],
  ["listing", "listing", row => row.listing || "None"],
  ["company_name", "company", row => row.company_name || "None"],
  ["company_id", "company record", row => row.company_id || "None"],
  ["kind", "type", row => capital(row.kind)],
  ["currency", "currency", row => row.currency],
  ["account_id", "account", row => row.account_id],
  ["etf_role", "fund type", row => ROLE[row.etf_role ?? ""] || "Not set"],
  ["shares", "shares", row => row.shares ?? "None"],
  ["cash", "balance", row => money(row.cash ?? null, row.currency)],
  ["mark", "price mark", row => (row.mark ? `${money(row.mark.value, row.currency)} (${fullDate(row.mark.as_of)})` : "None")],
];

/** What an identity correction can learn about a holding still to identify. */
const UNRESOLVED_FIELDS: [keyof UnresolvedHolding, string][] = [
  ["listing", "exchange"], ["kind", "type"], ["currency", "currency"], ["shares", "shares"], ["average_cost", "average cost"], ["candidates", "possible listings"],
];

/** The proposal's differences as rows a person can read, from the backend's own before and after values. Nothing
 *  is valued or estimated here: amounts are the quantities and balances the records hold. */
export function changeRows(proposal: Pick<PortfolioProposal, "differences">, snapshot: Snapshot | null): ChangeRow[] {
  const added = proposal.differences.filter(item => item.kind === "position_added").map(item => item.after as Position);
  const accounts = [...(snapshot?.accounts ?? []), ...proposal.differences.filter(item => item.kind === "account_added").map(item => item.after as Account)];
  const account = (id: string) => accounts.find(item => item.id === id)?.name || id;
  const position = (id: string) => snapshot?.positions.find(row => row.id === id) || added.find(row => row.id === id);
  const rows: ChangeRow[] = [];
  const push = (item: DifferenceItem, subject: string, detail: string, before: string, after: string, change: ChangeRow["change"]) =>
    rows.push({ key: `${item.path}:${detail}:${rows.length}`, subject, detail, before, after, change });

  for (const item of proposal.differences) {
    switch (item.kind) {
      case "position_added":
      case "position_removed": {
        const row = (item.after ?? item.before) as Position;
        const amount = row.kind === "cash" ? money(row.cash ?? null, row.currency) : shares(row.shares);
        const where = `${account(row.account_id)} · ${row.kind === "cash" ? "cash balance" : "holding"}`;
        if (item.kind === "position_added") push(item, name(row), where, row.kind === "cash" ? "None" : "Not held", amount, "added");
        else push(item, name(row), where, amount, "Removed", "removed");
        break;
      }
      case "position_changed": {
        const before = item.before as Position, after = item.after as Position;
        for (const [field, label, show] of FIELDS)
          if (JSON.stringify(before[field] ?? null) !== JSON.stringify(after[field] ?? null)) {
            const was = field === "account_id" ? account(before.account_id) : show(before);
            const now = field === "account_id" ? account(after.account_id) : show(after);
            push(item, name(after), `${account(after.account_id)} · ${label}`, was, now, "changed");
          }
        break;
      }
      case "unresolved_added":
      case "unresolved_removed":
      case "unresolved_changed": {
        const before = item.before as UnresolvedHolding | null, after = item.after as UnresolvedHolding | null;
        const row = (after ?? before)!;
        const where = `${account(row.account_id)} · to identify`;
        if (!before) push(item, row.ticker, where, "Not listed", shares(after!.shares), "added");
        else if (!after) {
          const identified = added.some(item => item.account_id === row.account_id && item.ticker === row.ticker);
          push(item, row.ticker, where, shares(before.shares), identified ? "Identified" : "Removed", "removed");
        } else
          for (const [field, label] of UNRESOLVED_FIELDS)
            if (JSON.stringify(before[field] ?? null) !== JSON.stringify(after[field] ?? null))
              push(item, row.ticker, `${where}, ${label}`, known(before[field]), known(after[field]), "changed");
        break;
      }
      case "cost_added":
      case "cost_removed":
      case "cost_changed": {
        const id = item.path.slice("average_costs[".length, -1);
        const held = position(id);
        const show = (value: unknown) => (value == null ? "Not given" : money(String(value), held?.currency || ""));
        push(item, held ? name(held) : id, "Average cost", show(item.before), item.after == null ? "Removed" : show(item.after), changeOf(item.kind));
        break;
      }
      case "fx_added":
      case "fx_removed":
      case "fx_changed": {
        const fx = (item.after ?? item.before) as FX;
        push(item, `${fx.from_currency} to ${fx.to_currency}`, "Exchange rate", rate(item.before as FX | null), item.after ? rate(item.after as FX) : "Removed", changeOf(item.kind));
        break;
      }
      case "settings_set":
      case "settings_changed": {
        const before = (item.before ?? {}) as PortfolioSettings, after = (item.after ?? {}) as PortfolioSettings;
        for (const [label, show] of SETTINGS)
          if (show(before) !== show(after)) push(item, label, "Rule", capital(show(before)), capital(show(after)), "changed");
        break;
      }
      case "benchmark_set":
      case "benchmark_changed":
        push(item, "Benchmark", "Rule", (item.before as BenchmarkSetting | null)?.ticker || "None", (item.after as BenchmarkSetting | null)?.ticker || "None", "changed");
        break;
      case "date_set":
      case "date_changed":
        push(item, "Holdings date", "Portfolio", item.before ? fullDate(String(item.before)) : "None", fullDate(String(item.after)), "changed");
        break;
      case "currency_set":
      case "currency_changed":
        push(item, "Reporting currency", "Portfolio", item.before ? String(item.before) : "None", String(item.after), "changed");
        break;
      case "account_added":
      case "account_removed":
      case "account_changed": {
        const before = item.before as Account | string | null, after = item.after as Account | string | null;
        if (item.kind === "account_changed") push(item, String(after), "Account name", String(before), String(after), "changed");
        else if (after) push(item, (after as Account).name, "Account", "None", "Added", "added");
        else push(item, (before as Account).name, "Account", "Held", "Removed", "removed");
        break;
      }
      default:
        // A kind this screen does not know yet is still shown, in the backend's own words.
        push(item, item.description || item.path, "Other change", "See description", "Changed", "changed");
    }
  }
  return rows;
}

const GROUPS: [string, (kind: string) => boolean][] = [
  ["Holdings date", kind => kind.startsWith("date_")],
  ["Rules and benchmark", kind => kind.startsWith("settings_") || kind.startsWith("benchmark_")],
  ["Average costs", kind => kind.startsWith("cost_")],
];

/** The parts of the saved portfolio this proposal leaves as they are. A first portfolio has nothing to leave. */
export function unchangedGroups(proposal: Pick<PortfolioProposal, "differences" | "base_revision">): string[] {
  if (proposal.base_revision == null) return [];
  return GROUPS.filter(([, touches]) => !proposal.differences.some(item => touches(item.kind))).map(([label]) => label);
}
