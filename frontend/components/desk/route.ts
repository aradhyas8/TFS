// Natural chat routing for the desk: which workflow a typed question becomes. Deterministic and local:
// a company is matched only against the saved holdings, and anything unclear is asked, never guessed.
import { isSupportedStock, type CandidateListing, type Position, type Snapshot } from "../../lib/contracts";

export type Route =
  | { kind: "review"; question: string }
  | { kind: "rebalance"; question: string }
  | { kind: "stock"; question: string; position: Position }
  | { kind: "candidate"; question: string; ticker: string }
  | { kind: "new-cash"; question: string; amount?: string; currency?: string }
  | { kind: "theme"; question: string; name?: string }
  | { kind: "clarify"; question: string; message: string; options: Position[]; review: boolean; candidateListings?: CandidateListing[] };

// Words that make a question about the whole portfolio rather than one company.
const PORTFOLIO = /\b(portfolio|holdings|exposure|allocation|allocate|concentration|overlap|rebalanc\w*|diversif\w*|my rules|cash|money)\b/i;
// Only plain requests to change the portfolio's shape. "How concentrated am I?" describes, so it stays a review.
const REBALANCE = /\brebalanc\w*|\brestructur\w*\b.*\b(?:portfolio|holdings)\b|^\s*what should i (?:reduce|trim|sell)\s*[?.!]*$|\btoo concentrated\b/i;
// Phrases that ask about one company; the words after them name it.
const INTENT = /\b(?:what do you think (?:about|of)|think (?:about|of)|thoughts on|opinion (?:on|of)|what about|how about|should i (?:invest in|add(?: more)?(?: to)?|buy(?: more)?|sell|trim|hold|keep|reduce|exit)|what changed (?:with|at|for|in)|what'?s (?:new|happening) (?:with|at)|how is|how's|analy[sz]e|outlook for|valuation of)\s+(.+?)\s*[?.!]*$/i;
// Corporate-form words that say nothing about which company is meant.
const FORM = new Set(["inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "nv", "sa", "ag", "se", "group", "holdings", "the", "class", "lp", "llc", "trust"]);

const MONEY_ACTION = /\b(invest\w*|put|allocat\w*|new cash)\b/i;
const MONEY_QUESTION_NO_AMOUNT = /\b(?:new (?:cash|money)|(?:cash|money) to (?:invest|allocate|put|deploy)|where should (?:new )?(?:cash|money) go|where to (?:invest|put|allocate) (?:new )?(?:cash|money)|allocate (?:new )?(?:cash|money))\b/i;

const THEME_STOP_WORDS = new Set(["which", "what", "my", "our", "all", "these", "those", "some", "any", "no", "more", "other", "few", "the", "a", "an"]);

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

export function isCandidateTicker(text: string, anyCase = false): boolean {
  const trimmed = text.trim();
  const bare = trimmed.replace(/^\$/, "");
  if (!/^[A-Za-z][A-Za-z0-9.-]{0,11}(\.[A-Za-z]{1,4})?$/.test(bare)) return false;
  return anyCase || trimmed.startsWith("$") || (bare === bare.toUpperCase() && bare.length >= 2);
}

export function parseMoney(text: string, snapshot?: Snapshot | null): { amount?: string; currency?: string } {
  // Check for currency code/symbol + number (CAD or USD only)
  const prefixMatch = text.match(/(?:(C\$|CAD\$|CAD|US\$|USD\$|USD|\$))\s*([0-9][0-9,]*(?:\.[0-9]+)?)/i);
  if (prefixMatch) {
    const rawCur = prefixMatch[1].toUpperCase();
    const rawAmt = prefixMatch[2].replace(/,/g, "");
    let currency: string | undefined;
    if (rawCur.startsWith("C") || rawCur === "CAD") currency = "CAD";
    else if (rawCur.startsWith("US") || rawCur === "USD") currency = "USD";
    else if (rawCur === "$") currency = snapshot?.reporting_currency || "CAD";
    return { amount: rawAmt, currency };
  }

  // Check for number + currency code/word
  const suffixMatch = text.match(/([0-9][0-9,]*(?:\.[0-9]+)?)\s*(CAD|USD|dollars?)\b/i);
  if (suffixMatch) {
    const rawAmt = suffixMatch[1].replace(/,/g, "");
    const rawCur = suffixMatch[2].toUpperCase();
    let currency: string | undefined;
    if (rawCur === "CAD") currency = "CAD";
    else if (rawCur === "USD") currency = "USD";
    else if (rawCur.startsWith("DOLLAR")) currency = snapshot?.reporting_currency || "USD";
    return { amount: rawAmt, currency };
  }

  // Pure isolated number (e.g. "/new-cash 5000")
  const isolatedMatch = text.trim().match(/^([0-9][0-9,]*(?:\.[0-9]+)?)$/);
  if (isolatedMatch) {
    const rawAmt = isolatedMatch[1].replace(/,/g, "");
    if (Number(rawAmt) > 0) {
      return { amount: rawAmt, currency: snapshot?.reporting_currency || undefined };
    }
  }

  // A number specifically attached to invest/put/allocate/new cash:
  // e.g. "I have 2,000 to invest", "allocate 5000", "put 3000 into"
  const verbAmtMatch = text.match(/\b(?:invest|allocate|put)\s+(?:about\s+|around\s+)?([0-9][0-9,]*(?:\.[0-9]+)?)\b/i) ||
                       text.match(/\b([0-9][0-9,]*(?:\.[0-9]+)?)\s+(?:to\s+(?:invest|allocate|put)|of\s+new\s+cash)\b/i);
  if (verbAmtMatch) {
    const rawAmt = verbAmtMatch[1].replace(/,/g, "");
    if (Number(rawAmt) > 0) {
      return { amount: rawAmt, currency: snapshot?.reporting_currency || undefined };
    }
  }

  return {};
}

function cleanThemeName(raw: string): string {
  let themeName = raw.trim().replace(/^[?.!,;:]+|[?.!,;:]+$/g, "").trim();
  themeName = themeName.replace(/^(?:the|a|an|is|are)\s+/i, "").trim();
  themeName = themeName.replace(/\s+(?:a\s+good\s+idea|worth\s+it)$/i, "").trim();
  return themeName;
}

export function parseTheme(text: string): { matches: boolean; name?: string } {
  // 1. "worth a bet": e.g. "Is AI infrastructure worth a bet?", "Worth a bet on renewable energy?"
  if (/\bworth a bet\b/i.test(text)) {
    const onMatch = text.match(/\bworth a bet on\s+(.+?)\s*[?.!]*$/i);
    if (onMatch) {
      const name = cleanThemeName(onMatch[1]);
      return { matches: true, name: name || undefined };
    }
    const betMatch = text.match(/^(?:is|are)?\s*(.+?)\s+(?:really\s+)?worth a bet\s*[?.!]*$/i);
    if (betMatch) {
      const name = cleanThemeName(betMatch[1]);
      return { matches: true, name: name || undefined };
    }
    return { matches: true, name: undefined };
  }

  // 2. "... stocks": e.g. "AI infrastructure stocks", "defense stocks", "What about defense stocks?"
  const stocksMatch = text.match(/^(?:(?:what|how)\s+about\s+|thoughts\s+on\s+|opinion\s+on\s+|look\s+at\s+|explore\s+)?(.+?)\s+stocks\s*[?.!]*$/i);
  if (stocksMatch) {
    const candidate = cleanThemeName(stocksMatch[1]);
    const words = candidate.toLowerCase().split(/\s+/);
    if (words.length > 0 && !words.every(w => THEME_STOP_WORDS.has(w))) {
      return { matches: true, name: candidate || undefined };
    }
  }

  // 3. "theme": e.g. "theme cloud software", "explore the cybersecurity theme", "theme"
  if (/\btheme\b/i.test(text)) {
    const afterMatch = text.match(/\btheme(?:\s+(?:on|about|for|is))?\s+(.+?)\s*[?.!]*$/i);
    if (afterMatch) {
      const name = cleanThemeName(afterMatch[1]);
      return { matches: true, name: name || undefined };
    }
    const beforeMatch = text.match(/^(?:(?:explore|test|look at)\s+(?:the\s+)?)?(.+?)\s+theme\s*[?.!]*$/i);
    if (beforeMatch) {
      const name = cleanThemeName(beforeMatch[1]);
      if (name && !THEME_STOP_WORDS.has(name.toLowerCase())) {
        return { matches: true, name };
      }
    }
    return { matches: true, name: undefined };
  }

  // 4. "basket": e.g. "Is a basket of uranium miners a good idea?", "basket of clean tech"
  if (/\bbasket\b/i.test(text)) {
    const basketMatch = text.match(/\bbasket\s+(?:of\s+)?(.+?)(?:\s+(?:a\s+good\s+idea|worth\s+it|stocks?|investments?))?\s*[?.!]*$/i);
    if (basketMatch) {
      const name = cleanThemeName(basketMatch[1]);
      return { matches: true, name: name || undefined };
    }
    return { matches: true, name: undefined };
  }

  return { matches: false };
}

export function route(raw: string, snapshot: Snapshot | null): Route {
  const question = raw.trim();
  const stocks = (snapshot?.positions || []).filter(row => row.kind !== "cash" && (row.shares === undefined || row.shares === null || Number(row.shares) > 0));

  // 1. Slash commands: /review, /rebalance, /stock <ref>, /new-cash [text], /theme [text].
  const command = question.match(/^\/(\w[\w-]*)\s*(.*)$/s);
  if (command) {
    const name = command[1].toLowerCase();
    const arg = command[2].trim();
    if (name === "review") return { kind: "review", question: arg || "Review my portfolio" };
    if (name === "rebalance") return { kind: "rebalance", question: arg || "Should I rebalance my portfolio?" };
    if (name === "theme") return { kind: "theme", question, name: arg || undefined };
    if (name === "new-cash") {
      const parsed = parseMoney(arg, snapshot);
      const defaultQ = arg ? (/\b(invest|put|allocate|new cash)\b/i.test(arg) ? arg : `I have ${arg} to invest`) : "I have new cash to invest";
      return {
        kind: "new-cash",
        question: defaultQ,
        ...(parsed.amount ? { amount: parsed.amount } : {}),
        ...(parsed.currency ? { currency: parsed.currency } : {}),
      };
    }
    if (name === "stock") {
      if (!arg) return { kind: "clarify", question, options: stocks.filter(isSupportedStock), review: false, message: "Which holding should I analyze?" };
      const found = byTicker(arg, stocks, true);
      if (found.length) return resolved(question, found, arg, stocks);
      const named = byName(arg, stocks);
      if (named.length) return resolved(question, named, arg, stocks);
      if (isCandidateTicker(arg, true)) return { kind: "candidate", question, ticker: arg.replace(/^\$/, "") };
      return resolved(question, [], arg, stocks);
    }
  }

  // 2. Rebalance phrasing (unchanged).
  if (REBALANCE.test(question)) return { kind: "rebalance", question };

  // 3. Money phrasing: an amount together with invest, put, allocate, or "new cash" opens New Cash form prefilled and never sends.
  // A money question with no amount opens empty New Cash form.
  if (MONEY_ACTION.test(question)) {
    const parsed = parseMoney(question, snapshot);
    if (parsed.amount) {
      return {
        kind: "new-cash",
        question,
        amount: parsed.amount,
        currency: parsed.currency,
      };
    }
  }
  if (MONEY_QUESTION_NO_AMOUNT.test(question)) {
    return { kind: "new-cash", question };
  }

  // 4. A held ticker or name becomes stock (unchanged).
  // A capitalized or $ ticker, or a company-intent phrase whose subject matches no holding, becomes candidate.
  // Multiple held tickers in a portfolio-level question stays a review.
  const tickers = companies(byTicker(question, stocks, false));
  if (tickers.length === 1) return resolved(question, tickers, label(tickers[0]), stocks);
  if (tickers.length > 1) return PORTFOLIO.test(question) ? { kind: "review", question } : resolved(question, tickers, tickers.map(label).join(" and "), stocks);

  if (isCandidateTicker(question, false)) return { kind: "candidate", question, ticker: question.replace(/^\$/, "") };

  const intent = question.match(INTENT);
  const subject = intent?.[1]?.trim() || "";
  if (intent && subject) {
    if (PORTFOLIO.test(subject) || /\b(?:companies|stocks)\b/i.test(subject)) return { kind: "review", question };
    const named = byName(subject, stocks);
    if (named.length) return resolved(question, named, subject, stocks);
    if (/^(it|this|that|the market|everything|my)\b/i.test(subject)) return { kind: "review", question };
    if (isCandidateTicker(subject, false)) return { kind: "candidate", question, ticker: subject.replace(/^\$/, "") };
  }

  // 5. Theme phrasing ("theme", "worth a bet on …", "… stocks") opens Theme form prefilled with the name.
  // We check theme phrasing before loose candidate token matching so "AI infrastructure stocks" or
  // "Is AI infrastructure worth a bet?" correctly routes to theme rather than candidate ticker AI.
  const theme = parseTheme(question);
  if (theme.matches) return { kind: "theme", question, name: theme.name };

  // Unowned candidate ticker token in the question (e.g. standalone ticker or query mentioning ticker)
  const candidateTokens = (question.match(/\$?[A-Za-z][A-Za-z0-9.-]*/g) || [])
    .filter(token => isCandidateTicker(token, false) && !stocks.some(s => s.ticker === token.replace(/^\$/, "").toUpperCase()));
  if (candidateTokens.length === 1 && !PORTFOLIO.test(question)) {
    return { kind: "candidate", question, ticker: candidateTokens[0].replace(/^\$/, "") };
  }

  // 6. Otherwise, review (unchanged).
  // Unmatched company subject with no ticker shape returns a short clarify question.
  if (intent && subject) return resolved(question, [], subject, stocks);
  return { kind: "review", question };
}
