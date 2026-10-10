// Natural chat routing for the desk: which workflow a typed question becomes. Deterministic and local:
// a company is matched only against the saved holdings, and anything unclear is asked, never guessed.
import { isSupportedStock, type Position, type Snapshot } from "../../lib/contracts";

export type Route =
  | { kind: "review"; question: string }
  | { kind: "rebalance"; question: string }
  | { kind: "stock"; question: string; position: Position }
  | { kind: "clarify"; question: string; message: string; options: Position[]; review: boolean };

// Words that make a question about the whole portfolio rather than one company.
const PORTFOLIO = /\b(portfolio|holdings|exposure|allocation|allocate|concentration|overlap|rebalanc\w*|diversif\w*|my rules|cash|money)\b/i;
// Only plain requests to change the portfolio's shape. "How concentrated am I?" describes, so it stays a review.
const REBALANCE = /\brebalanc\w*|\brestructur\w*\b.*\b(?:portfolio|holdings)\b|^\s*what should i (?:reduce|trim|sell)\s*[?.!]*$|\btoo concentrated\b/i;
// Phrases that ask about one company; the words after them name it.
const INTENT = /\b(?:what do you think (?:about|of)|think (?:about|of)|thoughts on|opinion (?:on|of)|should i (?:add(?: more)?(?: to)?|buy(?: more)?|sell|trim|hold|keep|reduce|exit)|what changed (?:with|at|for|in)|what'?s (?:new|happening) (?:with|at)|how is|how's|analy[sz]e|outlook for|valuation of)\s+(.+?)\s*[?.!]*$/i;
// Corporate-form words that say nothing about which company is meant.
const FORM = new Set(["inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "nv", "sa", "ag", "se", "group", "holdings", "the", "class", "lp", "llc", "trust"]);

function nameWords(name: string): string[] {
  return name.toLowerCase().replace(/\/[a-z]+\/?/g, " ").replace(/[^a-z0-9& ]+/g, " ").split(/\s+/).filter(word => word && !FORM.has(word));
}

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const hasWord = (text: string, phrase: string) => new RegExp(`(^|[^a-z0-9])${escape(phrase)}($|[^a-z0-9])`, "i").test(text);

/** Holdings whose ticker the text names. Typed tickers must be capitals or $-prefixed, so "f" or "shop" in a sentence is not a ticker. */
function byTicker(text: string, stocks: Position[], anyCase: boolean): Position[] {
  const tokens = text.match(/\$?[A-Za-z][A-Za-z0-9.-]*/g) || [];
  return stocks.filter(row => row.ticker && tokens.some(token => {
    const dollar = token.startsWith("$");
    const bare = token.replace(/^\$/, "").replace(/\.[A-Za-z]{1,3}$/, "");
    return (dollar || anyCase ? bare.toUpperCase() : bare) === row.ticker;
  }));
}

/** Holdings whose company name the text names: the full name, or its first distinctive word. */
function byName(text: string, stocks: Position[]): Position[] {
  return stocks.filter(row => {
    const words = nameWords(row.company_name || "");
    return words.length > 0 && (hasWord(text, words.join(" ")) || (words[0].length >= 4 && hasWord(text, words[0])));
  });
}

/** One position per company; a company held in several accounts is analyzed through its largest lot. */
function companies(rows: Position[]): Position[] {
  const best = new Map<string, Position>();
  for (const row of rows) {
    const key = row.company_id || `${row.ticker}:${row.listing}`;
    const held = best.get(key);
    if (!held || Number(row.shares || 0) > Number(held.shares || 0)) best.set(key, row);
  }
  return [...best.values()];
}

const label = (row: Position) => row.ticker || row.company_name || row.id;

function resolved(question: string, found: Position[], reference: string, stocks: Position[]): Route {
  const matched = companies(found);
  if (matched.length === 1) {
    const [row] = matched;
    if (isSupportedStock(row)) return { kind: "stock", question, position: row };
    return { kind: "clarify", question, options: [], review: true,
      message: `${label(row)} is a fund, not a single company. Stock Analysis covers individual US and Canadian stocks; a portfolio review covers your funds.` };
  }
  if (matched.length > 1) return { kind: "clarify", question, options: matched.filter(isSupportedStock), review: false,
    message: `"${reference}" matches more than one holding. Which one should I analyze?` };
  return { kind: "clarify", question, options: stocks.filter(isSupportedStock), review: true,
    message: `I couldn't match "${reference}" to a holding in your saved portfolio. Stock Analysis works on companies you own. Which one did you mean?` };
}

export function route(raw: string, snapshot: Snapshot | null): Route {
  const question = raw.trim();
  const stocks = (snapshot?.positions || []).filter(row => row.kind !== "cash" && (row.shares === undefined || row.shares === null || Number(row.shares) > 0));
  const command = question.match(/^\/(\w[\w-]*)\s*(.*)$/s);
  if (command?.[1].toLowerCase() === "review") return { kind: "review", question: command[2].trim() || "Review my portfolio" };
  if (command?.[1].toLowerCase() === "rebalance") return { kind: "rebalance", question: command[2].trim() || "Should I rebalance my portfolio?" };
  if (command?.[1].toLowerCase() === "stock") {
    const reference = command[2].trim();
    if (!reference) return { kind: "clarify", question, options: stocks.filter(isSupportedStock), review: false, message: "Which holding should I analyze?" };
    const found = byTicker(reference, stocks, true);
    return resolved(question, found.length ? found : byName(reference, stocks), reference, stocks);
  }
  // Rebalancing covers every holding, so a ticker in the question doesn't narrow it to Stock Analysis.
  if (!command && REBALANCE.test(question)) return { kind: "rebalance", question };
  const tickers = companies(byTicker(question, stocks, false));
  const intent = question.match(INTENT);
  const subject = intent?.[1] || "";
  // A capitalized ticker is unambiguous; with several, a portfolio-level question stays a review.
  if (tickers.length === 1) return resolved(question, tickers, label(tickers[0]), stocks);
  if (tickers.length > 1) return PORTFOLIO.test(question) ? { kind: "review", question } : resolved(question, tickers, tickers.map(label).join(" and "), stocks);
  // Names only count when the question asks about a company, so "my Canadian exposure" stays a review.
  if (!intent || PORTFOLIO.test(subject)) return { kind: "review", question };
  const named = byName(subject, stocks);
  if (named.length) return resolved(question, named, subject, stocks);
  // "How is it going", "how's the market" and similar name no company.
  if (/^(it|this|that|the market|everything|my)\b/i.test(subject)) return { kind: "review", question };
  return resolved(question, [], subject, stocks);
}
