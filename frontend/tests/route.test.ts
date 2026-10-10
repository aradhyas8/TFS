import { test, expect } from "@playwright/test";
import { route, parseMoney, parseTheme } from "../components/desk/route";
import type { Snapshot, Position } from "../lib/contracts";

const ACME: Position = {
  id: "tfsa-acme",
  account_id: "tfsa",
  kind: "stock",
  currency: "USD",
  ticker: "ACME",
  listing: "XNAS",
  company_id: "acme",
  company_name: "Acme Corp",
  shares: "10",
};

const BROAD: Position = {
  id: "rrsp-broad",
  account_id: "rrsp",
  kind: "etf",
  currency: "USD",
  ticker: "BROAD",
  listing: "XNYS",
  etf_role: "diversified",
  shares: "20",
};

const SNAPSHOT: Snapshot = {
  as_of: "2026-09-30",
  reporting_currency: "CAD",
  accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
  positions: [
    ACME,
    BROAD,
    { id: "rrsp-cash", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
  ],
  fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker" }],
};

test.describe("route precedence step 1: slash commands", () => {
  test("/review routes to review", () => {
    expect(route("/review", SNAPSHOT)).toEqual({ kind: "review", question: "Review my portfolio" });
    expect(route("/review custom question", SNAPSHOT)).toEqual({ kind: "review", question: "custom question" });
  });

  test("/rebalance routes to rebalance", () => {
    expect(route("/rebalance", SNAPSHOT)).toEqual({ kind: "rebalance", question: "Should I rebalance my portfolio?" });
    expect(route("/rebalance my portfolio", SNAPSHOT)).toEqual({ kind: "rebalance", question: "my portfolio" });
  });

  test("/stock routes to held stock, candidate, or clarify", () => {
    expect(route("/stock", SNAPSHOT)).toMatchObject({ kind: "clarify", message: "Which holding should I analyze?" });
    expect(route("/stock ACME", SNAPSHOT)).toEqual({ kind: "stock", question: "/stock ACME", position: ACME });
    expect(route("/stock Acme Corp", SNAPSHOT)).toEqual({ kind: "stock", question: "/stock Acme Corp", position: ACME });
    expect(route("/stock NVDA", SNAPSHOT)).toEqual({ kind: "candidate", question: "/stock NVDA", ticker: "NVDA" });
    expect(route("/stock $NVDA", SNAPSHOT)).toEqual({ kind: "candidate", question: "/stock $NVDA", ticker: "NVDA" });
  });

  test("/new-cash routes to new-cash with optional amount and currency", () => {
    expect(route("/new-cash", SNAPSHOT)).toEqual({ kind: "new-cash", question: "I have new cash to invest" });
    expect(route("/new-cash C$2,000", SNAPSHOT)).toEqual({ kind: "new-cash", question: "I have C$2,000 to invest", amount: "2000", currency: "CAD" });
    expect(route("/new-cash 5000", SNAPSHOT)).toEqual({ kind: "new-cash", question: "I have 5000 to invest", amount: "5000", currency: "CAD" });
    expect(route("/new-cash US$3,000", SNAPSHOT)).toEqual({ kind: "new-cash", question: "I have US$3,000 to invest", amount: "3000", currency: "USD" });
  });

  test("/theme routes to theme with optional name", () => {
    expect(route("/theme", SNAPSHOT)).toEqual({ kind: "theme", question: "/theme", name: undefined });
    expect(route("/theme AI infrastructure", SNAPSHOT)).toEqual({ kind: "theme", question: "/theme AI infrastructure", name: "AI infrastructure" });
  });
});

test.describe("route precedence step 2: rebalance phrasing", () => {
  test("rebalance phrases route to rebalance", () => {
    expect(route("Should I rebalance my portfolio?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "Should I rebalance my portfolio?" });
    expect(route("What should I trim?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "What should I trim?" });
    expect(route("What should I sell?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "What should I sell?" });
    expect(route("What should I reduce?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "What should I reduce?" });
    expect(route("Is my portfolio too concentrated?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "Is my portfolio too concentrated?" });
    expect(route("Restructure portfolio holdings", SNAPSHOT)).toEqual({ kind: "rebalance", question: "Restructure portfolio holdings" });
  });

  test("rebalance phrasing beats money words", () => {
    expect(route("Should I rebalance my cash?", SNAPSHOT)).toEqual({ kind: "rebalance", question: "Should I rebalance my cash?" });
  });
});

test.describe("route precedence step 3: money phrasing", () => {
  test("amount with invest/put/allocate/new cash routes to new-cash prefilled", () => {
    expect(route("I have C$2,000 to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have C$2,000 to invest",
      amount: "2000",
      currency: "CAD",
    });
    expect(route("I have $5,000 to put into my TFSA", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have $5,000 to put into my TFSA",
      amount: "5000",
      currency: "CAD",
    });
    expect(route("Where should I allocate $10,000?", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "Where should I allocate $10,000?",
      amount: "10000",
      currency: "CAD",
    });
    expect(route("I have 2,000 to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have 2,000 to invest",
      amount: "2000",
      currency: "CAD",
    });
    expect(route("I have new cash of $3,000", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have new cash of $3,000",
      amount: "3000",
      currency: "CAD",
    });
    expect(route("I have US$4,500 to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have US$4,500 to invest",
      amount: "4500",
      currency: "USD",
    });
    expect(route("1000 USD to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "1000 USD to invest",
      amount: "1000",
      currency: "USD",
    });
  });

  test("non-monetary numbers with invest do not route to new cash", () => {
    expect(route("Should I invest in 2 companies?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "Should I invest in 2 companies?",
    });
  });

  test("money phrasing without amount routes to empty new-cash", () => {
    expect(route("I have new cash to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have new cash to invest",
    });
    expect(route("I have new cash", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have new cash",
    });
    expect(route("Where should new cash go?", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "Where should new cash go?",
    });
    expect(route("I have new money to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have new money to invest",
    });
    expect(route("I have cash to invest", SNAPSHOT)).toEqual({
      kind: "new-cash",
      question: "I have cash to invest",
    });
  });
});

test.describe("route precedence step 4: held stock, candidate, clarify", () => {
  test("held ticker or name becomes stock", () => {
    expect(route("What do you think about ACME?", SNAPSHOT)).toEqual({
      kind: "stock",
      question: "What do you think about ACME?",
      position: ACME,
    });
    expect(route("What changed with Acme?", SNAPSHOT)).toEqual({
      kind: "stock",
      question: "What changed with Acme?",
      position: ACME,
    });
    expect(route("Should I buy more ACME?", SNAPSHOT)).toEqual({
      kind: "stock",
      question: "Should I buy more ACME?",
      position: ACME,
    });
    expect(route("ACME", SNAPSHOT)).toEqual({
      kind: "stock",
      question: "ACME",
      position: ACME,
    });
  });

  test("unowned ticker query becomes candidate", () => {
    expect(route("What do you think about NVDA?", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "What do you think about NVDA?",
      ticker: "NVDA",
    });
    expect(route("What do you think about $NVDA?", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "What do you think about $NVDA?",
      ticker: "NVDA",
    });
    expect(route("Should I buy NVDA?", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "Should I buy NVDA?",
      ticker: "NVDA",
    });
    expect(route("NVDA", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "NVDA",
      ticker: "NVDA",
    });
    expect(route("$NVDA", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "$NVDA",
      ticker: "NVDA",
    });
    expect(route("SHOP.TO", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "SHOP.TO",
      ticker: "SHOP.TO",
    });
    expect(route("Will NVDA beat earnings?", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "Will NVDA beat earnings?",
      ticker: "NVDA",
    });
    expect(route("Why is $TSLA dropping?", SNAPSHOT)).toEqual({
      kind: "candidate",
      question: "Why is $TSLA dropping?",
      ticker: "TSLA",
    });
  });

  test("multiple held tickers in portfolio question stays review", () => {
    expect(route("How are ACME and BROAD balanced in my portfolio?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "How are ACME and BROAD balanced in my portfolio?",
    });
  });

  test("unknown company names with no ticker shape clarify with a single short question", () => {
    const res = route("What do you think about Shopify?", SNAPSHOT);
    expect(res.kind).toBe("clarify");
    if (res.kind === "clarify") {
      expect(res.message).toContain("Shopify");
      expect(res.message).toContain("Stock Analysis works on companies you own");
      expect(res.review).toBe(true);
    }
  });
});

test.describe("route precedence step 5: theme phrasing", () => {
  test("worth a bet phrases open theme form prefilled with theme name", () => {
    expect(route("Is AI infrastructure worth a bet?", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "Is AI infrastructure worth a bet?",
      name: "AI infrastructure",
    });
    expect(route("Worth a bet on renewable energy?", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "Worth a bet on renewable energy?",
      name: "renewable energy",
    });
  });

  test("... stocks phrasing opens theme form prefilled with theme name", () => {
    expect(route("AI infrastructure stocks", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "AI infrastructure stocks",
      name: "AI infrastructure",
    });
    expect(route("defense stocks", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "defense stocks",
      name: "defense",
    });
  });

  test("theme / basket phrasing opens theme form prefilled with theme name", () => {
    expect(route("theme cloud software", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "theme cloud software",
      name: "cloud software",
    });
    expect(route("Is a basket of uranium miners a good idea?", SNAPSHOT)).toEqual({
      kind: "theme",
      question: "Is a basket of uranium miners a good idea?",
      name: "uranium miners",
    });
  });
});

test.describe("route precedence step 6: review fallback", () => {
  test("descriptive portfolio questions stay review", () => {
    expect(route("Review my portfolio", SNAPSHOT)).toEqual({
      kind: "review",
      question: "Review my portfolio",
    });
    expect(route("How concentrated am I?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "How concentrated am I?",
    });
    expect(route("What is my Canadian exposure?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "What is my Canadian exposure?",
    });
    expect(route("How much cash do I have?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "How much cash do I have?",
    });
    expect(route("How is the market?", SNAPSHOT)).toEqual({
      kind: "review",
      question: "How is the market?",
    });
  });
});
