"use client";

import { Banknote, Bookmark, ChartPie, Lightbulb, Menu, Scale, TrendingUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  BENCHMARK_FUND,
  del,
  EMPTY_THEME,
  get,
  isSupportedStock,
  portfolioReviewComparison,
  post,
  prepareUpdate,
  listProposals,
  confirmProposal,
  cancelProposal,
  createArtifact,
  themeComparison,
  type Analysis,
  type ArtifactReference,
  type BenchmarkSetting,
  type CandidateResult,
  type ConfirmationResult,
  type DecisionAction,
  type DecisionSubject,
  type DecisionWorkflow,
  type NewCashInput,
  type PortfolioProposal,
  type ProposalReceipt,
  type PortfolioSettings,
  type Position,
  type PriceRefresh,
  type RevisionedPortfolio,
  type SavedDecision,
  type SavedPortfolio,
  type Snapshot,
  type ThemeInput,
  type UnresolvedHolding,
} from "../../lib/contracts";
import Connection from "../../components/desk/Connection";
import Rail, { type DeskView } from "../../components/desk/Rail";
import Composer from "../../components/desk/Composer";
import Register from "../../components/desk/Register";
import Panel, { evidenceFor, ImportCsv } from "../../components/desk/Panel";
import PortfolioPreview from "../../components/desk/PortfolioPreview";
import { ORIGIN, isStale } from "../../components/desk/proposal";
import PortfolioView from "../../components/desk/PortfolioView";
import { draftEdits, ruleChecks, valuationFor } from "../../components/desk/portfolio";
import {
  Answer,
  Clarify,
  Echo,
  Failure,
  Historical,
  RebalanceAnswer,
  ReviewAnswer,
  StockAnswer,
  ThemeAnswer,
  Waiting,
  type Tab,
} from "../../components/desk/Memo";
import { isConversation, route, type Route } from "../../components/desk/route";
import Thread, { type ClarificationForm } from "../../components/desk/Thread";
import threadStyles from "../../components/desk/Thread.module.css";
import { Alert } from "@/components/arc/alert/alert";
import { Button } from "@/components/arc/button/button";
import { Checkbox } from "@/components/arc/checkbox/checkbox";
import Toast from "@/components/arc/toast/toast";
import { useConversation } from "../../components/desk/useConversation";
import { clock, decisionLabel, fullDate, newCashLabel, pct, shortDate } from "../../components/desk/format";

/** A question asked about the unsaved draft: the draft as captured, and where the question was asked. */
type DraftQuestion = {
  id: string;
  /** The view it was asked in; its progress and any failure show only there. */
  view: string;
  /** The thread it is sent to; null starts a new conversation. */
  destination: string | null;
  text: string;
  workflowInput?: Record<string, unknown>;
  portfolio: SavedPortfolio;
};

/** A question and its workflow: new cash with its input, Stock Analysis of one holding (position ID), a rebalance, theme discovery, else a portfolio review.
 *  Each one is a fresh analysis of the saved portfolio; nothing from an earlier answer is carried over. */
type Sent = {
  question: string;
  newCash: NewCashInput | null;
  theme?: ThemeInput | null;
  themeCandidates?: Position[];
  stock?: string | null;
  candidate?: Position | null;
  rebalance?: boolean;
  isHypothetical?: boolean;
  artifactReference?: ArtifactReference | null;
  snapshot?: Snapshot | null;
};
const EMPTY_CASH: NewCashInput = {
  amount: null,
  cash_position_id: null,
  account_id: null,
  currency: null,
  confirmed: false,
  risk_context: null,
};
const durationsKey = (sent: Sent | null) =>
  sent?.newCash
    ? "desk.newCashDurations"
    : sent?.theme
      ? "desk.themeDurations"
      : sent?.stock
        ? "desk.stockDurations"
        : sent?.rebalance
          ? "desk.rebalanceDurations"
          : "desk.reviewDurations";
const TITLE = "Analyst";
// The last answer, so a reload doesn't lose it. Per-browser convenience; Save puts it in Decisions.
const ANSWER_KEY = "desk.lastAnswer";

/** Only rules the user actually set; an all-unset rule set is sent and saved as none. */
function storedSettings(settings: PortfolioSettings): PortfolioSettings | null {
  const baseline = settings.baseline;
  const hasBaseline =
    baseline &&
    Object.values(baseline).some(value => value !== undefined && value !== null);
  const cleaned: PortfolioSettings = {
    ...settings,
    baseline: hasBaseline ? baseline : null,
  };
  const hasAny = Object.entries(cleaned).some(([k, v]) => {
    if (k === "baseline") return v !== null && v !== undefined;
    return v !== undefined && v !== null;
  });
  return hasAny ? cleaned : null;
}

/** Which workflow computed a result, read from the result itself. */
function workflowOf(result: Analysis): DecisionWorkflow {
  if (result.reunderwriting) return "rebalance";
  if (result.stock && !result.allocation) return "stock";
  if (result.theme) return "theme";
  return result.allocation ? "new_cash" : "review";
}

const WORKFLOW_TITLE: Record<DecisionWorkflow, string> = { stock: "Stock analysis", new_cash: "New cash", review: "Portfolio review", rebalance: "Rebalance", theme: "Theme" };

/** Whether a media query matches. False until mounted, like every width-dependent layout here. */
function useMedia(query: string) {
  const [matches, setMatches] = useState(false);
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

const FOCUSABLE = "a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex='-1'])";

function pastDurations(sent: Sent | null): number[] {
  try {
    return JSON.parse(localStorage.getItem(durationsKey(sent)) || "[]")
      .filter((n: unknown) => typeof n === "number")
      .slice(-3);
  } catch {
    return [];
  }
}

function isDraftDirty(
  committed: SavedPortfolio | null,
  draft: {
    snapshot: Snapshot | null;
    settings: PortfolioSettings;
    benchmark: BenchmarkSetting | null;
  }
): boolean {
  if (!committed || !draft.snapshot) return false;
  const committedSettings = storedSettings(committed.settings || {}) || {};
  const draftSettingsClean = storedSettings(draft.settings) || {};
  if (JSON.stringify(committedSettings) !== JSON.stringify(draftSettingsClean)) return true;

  const committedBenchmark = committed.benchmark ?? null;
  const draftBenchmark = draft.benchmark ?? null;
  if (JSON.stringify(committedBenchmark) !== JSON.stringify(draftBenchmark)) return true;

  if (committed.snapshot && draft.snapshot) {
    const cRoles = committed.snapshot.positions.map(p => ({ id: p.id, role: p.etf_role ?? null }));
    const dRoles = draft.snapshot.positions.map(p => ({ id: p.id, role: p.etf_role ?? null }));
    if (JSON.stringify(cRoles) !== JSON.stringify(dRoles)) return true;
  }
  return false;
}

function reconcileDraftSettings(
  currentDraft: PortfolioSettings,
  committed: PortfolioSettings,
  proposal?: PortfolioProposal | null
): PortfolioSettings {
  if (!proposal) return committed;
  const changedSettingKeys = new Set(
    proposal.differences
      .filter(d => d.path.startsWith("settings"))
      .map(d => d.path.replace(/^settings\.?/, "").split(".")[0])
  );
  if (proposal.differences.some(d => d.path === "settings")) {
    return committed;
  }
  const merged: PortfolioSettings = { ...committed };
  for (const [key, value] of Object.entries(currentDraft)) {
    if (!changedSettingKeys.has(key) && value !== undefined && value !== null) {
      (merged as any)[key] = value;
    }
  }
  return merged;
}

function reconcileDraftBenchmark(
  currentDraft: BenchmarkSetting | null,
  committed: BenchmarkSetting | null,
  proposal?: PortfolioProposal | null
): BenchmarkSetting | null {
  if (!proposal) return committed;
  const benchmarkChanged = proposal.differences.some(d => d.path.startsWith("benchmark"));
  if (benchmarkChanged) {
    return committed;
  }
  return currentDraft;
}

function reconcileDraftSnapshot(
  currentDraft: Snapshot | null,
  committed: Snapshot,
  proposal?: PortfolioProposal | null
): Snapshot {
  if (!currentDraft || !proposal) return committed;
  const draftRoles = new Map(currentDraft.positions.map(p => [p.id, p.etf_role]));
  const positions = committed.positions.map(p => {
    const wasInDiff = proposal.differences.some(d => d.path.includes(p.id));
    if (!wasInDiff && draftRoles.has(p.id) && draftRoles.get(p.id) !== undefined) {
      return { ...p, etf_role: draftRoles.get(p.id) };
    }
    return p;
  });
  return { ...committed, positions };
}

export default function DeskPage() {
  // Committed portfolio state (durable SQLite storage)
  const [committedRevision, setCommittedRevision] = useState<number | null>(null);
  const [committedPortfolio, setCommittedPortfolio] = useState<SavedPortfolio | null>(null);
  const [committedSnapshot, setCommittedSnapshot] = useState<Snapshot | null>(null);
  const [committedSettings, setCommittedSettings] = useState<PortfolioSettings>({});
  const [committedBenchmark, setCommittedBenchmark] = useState<BenchmarkSetting | null>(null);
  const [committedAverageCosts, setCommittedAverageCosts] = useState<Record<string, string>>({});
  const [committedUnresolved, setCommittedUnresolved] = useState<UnresolvedHolding[]>([]);

  // Draft portfolio state (session-local working draft)
  const [draftSnapshot, setDraftSnapshot] = useState<Snapshot | null>(null);
  const [draftSettings, setDraftSettings] = useState<PortfolioSettings>({});
  const [draftBenchmark, setDraftBenchmark] = useState<BenchmarkSetting | null>(null);
  const [draftAverageCosts, setDraftAverageCosts] = useState<Record<string, string>>({});
  const [draftUnresolved, setDraftUnresolved] = useState<UnresolvedHolding[]>([]);

  // Proposal review and confirmation state
  const [proposals, setProposals] = useState<PortfolioProposal[]>([]);
  const [proposalsLoaded, setProposalsLoaded] = useState(false);
  const [proposalsFailed, setProposalsFailed] = useState(false);
  const [activeProposal, setActiveProposal] = useState<PortfolioProposal | null>(null);
  const [pendingProposals, setPendingProposals] = useState<PortfolioProposal[]>([]);
  const [reviewing, setReviewing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  // The backend refused to confirm the open proposal because the portfolio moved on since it was prepared.
  const [proposalRefused, setProposalRefused] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // The receipt of the last confirmation, shown once; the revision itself is shown wherever the portfolio is.
  const [receipt, setReceipt] = useState<ProposalReceipt | null>(null);
  // A cancel started from a list of waiting changes that failed: shown on that proposal's row, where it was asked.
  const [cancelFailure, setCancelFailure] = useState<{ id: string; message: string } | null>(null);
  // A second click, or a click while the first confirmation is in flight, sends nothing.
  const confirmingRef = useRef(false);

  // Refreshes start from several places. Only the latest one may report: an older request that fails late
  // must not hide a list a newer request already loaded.
  const proposalRefresh = useRef(0);
  function refreshProposals() {
    const request = ++proposalRefresh.current;
    setProposalsFailed(false);
    listProposals()
      .then(res => {
        if (request !== proposalRefresh.current) return;
        setProposals(res);
        setProposalsLoaded(true);
        setPendingProposals(res.filter(p => p.status === "pending"));
      })
      .catch(() => {
        if (request !== proposalRefresh.current) return;
        // The earlier list is dropped: the conversation shows the failure rather than states it can no longer vouch for.
        setProposalsLoaded(false);
        setProposalsFailed(true);
      });
  }

  function openProposal(proposal: PortfolioProposal) {
    setActiveProposal(proposal);
    setProposalRefused(false);
    setPreviewError(null);
  }


  const [restoring, setRestoring] = useState(true);
  const [workflow, setWorkflow] = useState<"new-cash" | "theme" | null>(null);
  const [question, setQuestion] = useState("");
  const [newCash, setNewCash] = useState<NewCashInput>(EMPTY_CASH);
  const [theme, setTheme] = useState<ThemeInput>(EMPTY_THEME);
  const [themeCandidates, setThemeCandidates] = useState<Position[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [run, setRun] = useState<(Sent & { id: number; startedAt: number }) | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [past, setPast] = useState<number[]>([]);
  // A committed answer keeps the revision it was computed from, so it never values a later revision.
  const [answer, setAnswer] = useState<(Sent & { analysis: Analysis; revision?: number | null }) | null>(null);
  const [failure, setFailure] = useState<(Sent & { message: string }) | null>(null);
  const [clarify, setClarify] = useState<Extract<Route, { kind: "clarify" }> | null>(null);
  const [saved, setSaved] = useState<SavedDecision | null>(null);
  const [saving, setSaving] = useState(false);
  const [decisions, setDecisions] = useState<SavedDecision[]>([]);
  const [reopened, setReopened] = useState<SavedDecision | null>(null);
  // Portfolio and Decisions are views of their own; the conversation is the default. Any step that starts or
  // opens something in the conversation returns to it.
  const [view, setView] = useState<DeskView>("chat");
  const viewOpen = view !== "chat";
  const [decisionsState, setDecisionsState] = useState<"loading" | "ready" | "failed">("loading");
  const [tab, setTab] = useState<Tab>("holdings");
  const [focus, setFocus] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const detailsButton = useRef<HTMLButtonElement>(null);
  // The control that opened the details, so closing them returns there: a citation, a result tab, the Details button.
  const opener = useRef<HTMLElement | null>(null);
  // Below 1024 the rail is a drawer over the page; below 1280 the details are. While one covers the page,
  // everything behind it is inert and Tab stays inside it.
  const railOver = useMedia("(max-width: 1023px)");
  const detailsOver = useMedia("(max-width: 1279px)");
  const drawer = railOpen && railOver;
  // The rail drawer belongs to narrow layouts. Widening the window puts the rail back in its column and closes the
  // drawer, so narrowing again never reopens it over the page by itself.
  useEffect(() => { if (!railOver) setRailOpen(false); }, [railOver]);
  const sheet = panelOpen && detailsOver && !drawer;
  // Details are closed until something asks for them: a citation, a result tab, holdings, or the Details button.
  // Every opening path goes through here, so the panel shows at every width.
  const showDetails = (next?: Tab, focusId: string | null = null, takeFocus = false) => {
    // The latest trigger outside the details wins, so a second citation clicked beside open details is the one returned to.
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body && !active.closest("#details")) opener.current = active;
    else if (!panelOpen) opener.current = null;
    if (next) setTab(next);
    setFocus(focusId);
    setPanelOpen(true);
    // Covering the page, the details take focus; beside the conversation they leave it where it is.
    if (takeFocus || detailsOver)
      requestAnimationFrame(() => document.querySelector<HTMLElement>("#details [role=tab][aria-selected=true]")?.focus());
  };
  const restoreFocus = () =>
    requestAnimationFrame(() => {
      const back = opener.current;
      opener.current = null;
      (back?.isConnected && back.getClientRects().length && !back.closest("[inert]") ? back : detailsButton.current)?.focus();
    });
  const closeDetails = () => {
    setPanelOpen(false);
    restoreFocus();
  };
  const closeRail = () => {
    setRailOpen(false);
    requestAnimationFrame(() => menuButton.current?.focus());
  };
  // Escape closes the rail drawer first, then the details. Tab cycles inside whichever covers the page.
  // The review dialog handles its own keys.
  useEffect(() => {
    if (!railOpen && !panelOpen) return;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector("[role=dialog]")) return;
      if (event.key === "Tab" && (drawer || sheet)) {
        const box = document.getElementById(drawer ? "rail" : "details");
        const items = [...(box?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])].filter(item => item.getClientRects().length);
        if (!box || !items.length) return;
        const inside = box.contains(document.activeElement);
        if (event.shiftKey && (!inside || document.activeElement === items[0])) {
          event.preventDefault();
          items.at(-1)!.focus();
        } else if (!event.shiftKey && (!inside || document.activeElement === items.at(-1))) {
          event.preventDefault();
          items[0].focus();
        }
        return;
      }
      if (event.key !== "Escape") return;
      if (railOpen) {
        setRailOpen(false);
        requestAnimationFrame(() => menuButton.current?.focus());
      } else {
        setPanelOpen(false);
        restoreFocus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [railOpen, panelOpen, drawer, sheet]);
  const [notice, setNotice] = useState("");
  const [prices, setPrices] = useState<PriceRefresh | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const runId = useRef(0);
  // Durable conversation: threads, messages and runs live in the backend, never in the cached last answer.
  const conversation = useConversation();
  // The stored result the Details panel reads while a conversation is on screen: the one the investor last opened.
  const [threadResult, setThreadResult] = useState<{ id: string; analysis: Analysis; revision?: number | null; basis?: ArtifactReference["basis"] } | null>(null);
  // Which decision each stored result was saved as in this session: artifact ID to decision ID. The decision
  // itself is always read from the decisions list, so a deleted decision is no longer shown as saved.
  // ponytail: kept in memory, so after a reload a result can be saved a second time. Record the artifact ID
  // on the decision if duplicate register entries become a problem.
  const [savedResults, setSavedResults] = useState<Record<string, string>>({});
  const [savingResult, setSavingResult] = useState<string | null>(null);
  const [saveFailed, setSaveFailed] = useState<string | null>(null);
  // A workflow form opened from a clarification: its submission is a new run in this conversation.
  const [resuming, setResuming] = useState(false);
  // Explicit choice, for one question: analyze the unsaved draft as a hypothetical portfolio.
  const [useDraft, setUseDraft] = useState(false);
  // Storing the draft is a request, and the investor may navigate while it runs. Its progress and its failure
  // belong to the view the question was asked in, named by that view's key, and show nowhere else.
  const [capturing, setCapturing] = useState<string[]>([]);
  const [captureFailed, setCaptureFailed] = useState<Record<string, DraftQuestion>>({});
  // By question, not by view: the view's key changes when a new conversation receives its thread.
  const forgetFailed = (id: string) =>
    setCaptureFailed(failed => Object.fromEntries(Object.entries(failed).filter(([, asked]) => asked.id !== id)));
  // A new conversation that received its thread keeps its failed question, which now continues that thread.
  useEffect(() => {
    const moved = conversation.rekeyed;
    if (!moved) return;
    setCaptureFailed(failed => {
      const kept = failed[moved.from];
      if (!kept) return failed;
      const others = Object.fromEntries(Object.entries(failed).filter(([key]) => key !== moved.from));
      return { ...others, [moved.to]: { ...kept, view: moved.to, destination: moved.threadId } };
    });
  }, [conversation.rekeyed]);
  const threadEnd = useRef<HTMLDivElement>(null);
  const wasRunning = useRef(false);

  // A finished conversation run may have prepared a portfolio proposal; it is confirmed through the existing preview.
  useEffect(() => {
    if (wasRunning.current && !conversation.running) refreshProposals();
    wasRunning.current = conversation.running;
  }, [conversation.running]);

  useEffect(() => setThreadResult(null), [conversation.openId]);
  useEffect(() => {
    if (!workflow) setResuming(false);
  }, [workflow]);
  // A proposal prepared mid-run is listed as soon as its message arrives, so its review can be opened.
  const proposalCount = conversation.thread?.messages.reduce((count, item) => count + item.proposal_ids.length, 0) ?? 0;
  useEffect(() => {
    if (proposalCount) refreshProposals();
  }, [proposalCount]);

  // Anything new at the end of the thread (a message, a run outcome, a send that failed) is brought into view.
  const threadTail = [
    conversation.openId,
    conversation.thread?.messages.length,
    conversation.thread?.runs.at(-1)?.status,
    conversation.pending?.clientRequestId,
    conversation.sendError,
  ].join("|");
  useEffect(() => {
    threadEnd.current?.scrollIntoView({ block: "end" });
  }, [threadTail]);

  // The composer is disabled while an answer is on its way; hand the keyboard back when it can be used again.
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !conversation.busy) document.getElementById("ask")?.focus();
    wasBusy.current = conversation.busy;
  }, [conversation.busy]);

  useEffect(() => {
    try {
      const last = localStorage.getItem(ANSWER_KEY);
      if (last) {
        setAnswer(JSON.parse(last));
        setCollapsed(true);
        setTab("evidence");
      }
    } catch {
      /* nothing to restore */
    }
    get<RevisionedPortfolio | SavedPortfolio | null>("/api/portfolio")
      .then(saved => {
        if (saved) adopt(saved);
      })
      .catch(() => setNotice("Your saved portfolio couldn't be loaded."))
      .finally(() => setRestoring(false));
    listProposals("pending")
      .then(setPendingProposals)
      .catch(() => {
        /* non-fatal */
      });
    get<SavedDecision[]>("/api/decisions")
      .then(loaded => {
        setDecisions(loaded);
        setDecisionsState("ready");
        const map: Record<string, string> = {};
        for (const d of loaded) {
          if (d.artifact_id) {
            map[d.artifact_id] = d.id;
          }
        }
        setSavedResults(prev => ({ ...prev, ...map }));
      })
      .catch(() => {
        setDecisionsState("failed");
        setNotice("Saved decisions couldn't be loaded.");
      });
    refreshProposals();
  }, []);

  useEffect(() => {
    if (!run) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [run]);

  useEffect(() => {
    const reset = () => {
      if (!document.hidden) document.title = TITLE;
    };
    document.addEventListener("visibilitychange", reset);
    return () => document.removeEventListener("visibilitychange", reset);
  }, []);

  // A changed committed portfolio invalidates the destination and the "this is new money" confirmation.
  useEffect(() => {
    setNewCash(current => ({
      ...current,
      confirmed: false,
      currency: current.currency || committedSnapshot?.reporting_currency || null,
      account_id: committedSnapshot?.accounts.some(account => account.id === current.account_id)
        ? current.account_id
        : committedSnapshot?.accounts.length === 1
          ? committedSnapshot.accounts[0].id
          : null,
    }));
  }, [committedSnapshot]);

  function adopt(data: RevisionedPortfolio | SavedPortfolio) {
    const isRevisioned = "revision" in data && "portfolio" in data;
    const rev = isRevisioned ? data.revision : null;
    const saved = isRevisioned ? data.portfolio : (data as SavedPortfolio);

    const restoredSettings = saved.settings || {};
    const restoredBenchmark = saved.benchmark ?? null;

    setCommittedRevision(rev);
    setCommittedPortfolio(saved);
    setCommittedSnapshot(saved.snapshot);
    setCommittedSettings(restoredSettings);
    setCommittedBenchmark(restoredBenchmark);
    setCommittedAverageCosts(saved.average_costs || {});
    setCommittedUnresolved(saved.unresolved || []);

    setDraftSnapshot(saved.snapshot);
    setDraftSettings(restoredSettings);
    setDraftBenchmark(restoredBenchmark);
    setDraftAverageCosts(saved.average_costs || {});
    setDraftUnresolved(saved.unresolved || []);

    if (saved.snapshot.positions.some(row => row.kind !== "cash") || saved.benchmark) {
      void refreshPrices(false);
    }
  }

  function adoptCommitted(result: ConfirmationResult, proposal?: PortfolioProposal | null) {
    const rev = result.portfolio.revision;
    const saved = result.portfolio.portfolio;

    const restoredSettings = saved.settings || {};
    const restoredBenchmark = saved.benchmark ?? null;

    setCommittedRevision(rev);
    setCommittedPortfolio(saved);
    setCommittedSnapshot(saved.snapshot);
    setCommittedSettings(restoredSettings);
    setCommittedBenchmark(restoredBenchmark);
    setCommittedAverageCosts(saved.average_costs || {});
    setCommittedUnresolved(saved.unresolved || []);

    // Reconcile draft state to preserve unrelated draft edits
    setDraftSettings(prevDraft => reconcileDraftSettings(prevDraft, restoredSettings, proposal));
    setDraftBenchmark(prevDraft => reconcileDraftBenchmark(prevDraft, restoredBenchmark, proposal));
    setDraftSnapshot(prevDraft => reconcileDraftSnapshot(prevDraft, saved.snapshot, proposal));
    setDraftAverageCosts(saved.average_costs || {});
    setDraftUnresolved(saved.unresolved || []);

    if (saved.snapshot.positions.some(row => row.kind !== "cash") || saved.benchmark) {
      void refreshPrices(false);
    }
  }

  // The portfolio view's values: only an analysis of exactly the committed revision. The stored result on
  // screen comes first, then the last desk answer, each by the revision it names.
  // Its rule checks count only while they were made against the rules committed now.
  const valued =
    valuationFor(threadResult?.analysis ?? null, threadResult ?? null, committedRevision, committedSnapshot) ??
    valuationFor(answer?.analysis ?? null, { revision: answer?.revision, basis: answer?.analysis.artifact_reference?.basis }, committedRevision, committedSnapshot);
  const valuation = valued && { ...valued, guardrails: ruleChecks(valued, committedSettings) };
  const edits = draftEdits(committedPortfolio, { snapshot: draftSnapshot, settings: draftSettings, benchmark: draftBenchmark });
  const isDirty = isDraftDirty(committedPortfolio, {
    snapshot: draftSnapshot,
    settings: draftSettings,
    benchmark: draftBenchmark,
  });
  // The draft is analyzed only when it was explicitly selected for the next question and still differs.
  const askingDraft = useDraft && isDirty && !!draftSnapshot;
  // The choice appears below the fold on a full screen; bring it into view when a draft edit first creates it.
  const draftNote = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (isDirty) draftNote.current?.scrollIntoView({ block: "center" });
  }, [isDirty]);

  async function refreshPrices(force: boolean) {
    setRefreshing(true);
    try {
      setPrices(await post<PriceRefresh>("/api/market/refresh", { force }));
    } catch (error) {
      setNotice(
        error instanceof Error
          ? `Prices couldn't be refreshed: ${error.message}`
          : "Prices couldn't be refreshed."
      );
    } finally {
      setRefreshing(false);
    }
  }

  async function reviewChanges() {
    if (!draftSnapshot) return;
    setReviewing(true);
    setNotice("");
    setPreviewError(null);
    try {
      const candidate: SavedPortfolio = {
        snapshot: draftSnapshot,
        average_costs: draftAverageCosts,
        settings: storedSettings(draftSettings),
        benchmark: draftBenchmark,
        unresolved: draftUnresolved,
        saved_at: committedPortfolio?.saved_at ?? null,
      };
      const res = await prepareUpdate({
        base_revision: committedRevision,
        portfolio: candidate,
      });
      if (res.status === "unchanged" || !res.proposal) {
        setNotice("No changes to review; your draft matches the committed portfolio.");
        return;
      }
      setActiveProposal(res.proposal);
      setProposalRefused(false);
      refreshProposals();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to prepare portfolio review.";
      if (msg.includes("409") || msg.toLowerCase().includes("stale")) {
        setNotice(
          "The saved portfolio was updated. Your draft edits are kept. Review them again against the current portfolio."
        );
      } else {
        setNotice(msg);
      }
    } finally {
      setReviewing(false);
    }
  }

  function handleProposal(proposal: PortfolioProposal) {
    setActiveProposal(proposal);
    setProposalRefused(false);
    setPreviewError(null);
    setPendingProposals(prev => [proposal, ...prev.filter(p => p.id !== proposal.id)]);
  }

  /** Takes in a confirmation the server already holds: the new committed portfolio and its receipt. */
  function adoptConfirmation(res: ConfirmationResult, proposal: PortfolioProposal) {
    adoptCommitted(res, proposal);
    setReceipt(res.receipt);
    setActiveProposal(null);
    setProposalRefused(false);
    setPreviewError(null);
    setPendingProposals(prev => prev.filter(p => p.id !== proposal.id));
    refreshProposals();
  }

  /** Cancels a proposal. If the server says it was already applied (a confirmation whose reply was lost), the
   *  receipt is fetched instead, since confirming an applied proposal returns it, and the page shows what was saved.
   *  Any other failure is passed to `report` and the proposal stays where it was. */
  async function cancelOnServer(proposal: PortfolioProposal, report: (message: string) => void): Promise<boolean> {
    try {
      await cancelProposal(proposal.id);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (/already been applied/i.test(msg)) {
        try {
          adoptConfirmation(await confirmProposal(proposal.id), proposal);
        } catch {
          setNotice("This change was already saved. Reload the page to see it.");
        }
      } else if (/cancelled/i.test(msg)) return true;
      else report(msg || "The proposal couldn't be cancelled. Try again.");
      return false;
    }
  }

  async function cancelPendingProposal(proposalId: string) {
    const proposal = pendingProposals.find(p => p.id === proposalId);
    if (!proposal) return;
    setCancelFailure(null);
    if (!(await cancelOnServer(proposal, message => setCancelFailure({ id: proposalId, message })))) return;
    setPendingProposals(prev => prev.filter(p => p.id !== proposalId));
    if (activeProposal?.id === proposalId) closeProposal();
    refreshProposals();
  }

  async function confirmActiveProposal() {
    if (!activeProposal || confirmingRef.current) return;
    confirmingRef.current = true;
    const confirmed = activeProposal;
    setConfirming(true);
    setPreviewError(null);
    try {
      adoptConfirmation(await confirmProposal(confirmed.id), confirmed);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "";
      if (/stale|409|conflict/i.test(msg)) {
        // Refused, never rebased: the proposal stays as it was prepared and the draft is untouched.
        setProposalRefused(true);
        refreshProposals();
      } else if (!(err instanceof Error) || err instanceof TypeError || err.name === "TimeoutError" || err.name === "AbortError") {
        // The request may have reached the server. Confirming again is safe: an applied proposal returns its receipt.
        setPreviewError("The confirmation got no reply, so it isn't known whether it was saved. Confirm again to check; a proposal is never applied twice.");
      } else {
        setPreviewError(msg);
      }
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  }

  async function cancelActiveProposal() {
    if (!activeProposal || confirmingRef.current) return;
    const cancelling = activeProposal;
    setCancelling(true);
    setPreviewError(null);
    const done = await cancelOnServer(cancelling, setPreviewError);
    setCancelling(false);
    if (!done) return;
    setPendingProposals(prev => prev.filter(p => p.id !== cancelling.id));
    closeProposal();
    refreshProposals();
  }

  /** Closes the review and leaves the proposal waiting, to be reviewed again from the list of changes. */
  function closeProposal() {
    setActiveProposal(null);
    setProposalRefused(false);
    setPreviewError(null);
  }

  function discardDraft() {
    if (!committedPortfolio) return;
    setDraftSnapshot(committedSnapshot);
    setDraftSettings(committedSettings);
    setDraftBenchmark(committedBenchmark);
    setDraftAverageCosts(committedAverageCosts);
    setDraftUnresolved(committedUnresolved);
    setNotice("");
  }

  const showThread =
    !viewOpen && !reopened && !run && !clarify && !failure && (!!conversation.openId || !!conversation.pending);
  // Changes waiting for confirmation. One prepared in the conversation on screen is shown by its own card, which
  // offers its review; an out-of-date one stays listed here so it can still be opened and cancelled.
  const waitingHere = pendingProposals.filter(p =>
    !(showThread && p.thread_id && p.thread_id === conversation.openId && !isStale(p, committedRevision)));
  const result = (showThread ? threadResult?.analysis : answer?.analysis) ?? null;
  const evidence = evidenceFor(reopened ? null : result, reopened);
  const cite = {
    numbers: evidence.numbers,
    onCite: (id: string) => showDetails("evidence", id),
  };
  const openTab = (next: Tab) => showDetails(next);

  /** Ordinary analysis uses COMMITTED portfolio state */
  async function analyze(sent: Sent) {
    if (!committedSnapshot) return;
    const revision = committedRevision;
    const id = ++runId.current;
    const startedAt = Date.now();
    conversation.close();
    setRun({ ...sent, id, startedAt });
    setNow(startedAt);
    setPast(pastDurations(sent));
    setFailure(null);
    setClarify(null);
    setSaved(null);
    setReopened(null);
    setView("chat");
    setCollapsed(true);
    setNotice("");
    try {
      const dated =
        committedSnapshot.positions.some(row => row.mark) || committedSnapshot.fx.length > 0;
      const base = dated
        ? committedSnapshot
        : { ...committedSnapshot, as_of: new Date().toLocaleDateString("en-CA") };
      let benchmarkRow: Position | null = null;
      if (committedBenchmark) {
        let accountId = committedSnapshot.accounts[0]?.id || "default";
        let includeBenchmark = true;
        if (sent.newCash) {
          const destCurrency = sent.newCash.currency || committedSnapshot.reporting_currency;
          if (committedBenchmark.currency !== destCurrency) {
            includeBenchmark = false;
          } else {
            accountId = sent.newCash.account_id || committedSnapshot.accounts[0]?.id || "default";
          }
        }
        if (includeBenchmark) {
          benchmarkRow = {
            id: BENCHMARK_FUND,
            account_id: accountId,
            kind: "etf",
            etf_role: "diversified",
            currency: committedBenchmark.currency,
            ticker: committedBenchmark.ticker,
            listing: committedBenchmark.listing,
            company_name: committedBenchmark.name || undefined,
            shares: "0",
          };
        }
      }
      const portfolio = {
        ...base,
        positions: [
          ...base.positions,
          ...(benchmarkRow ? [benchmarkRow] : []),
          ...(sent.candidate ? [sent.candidate] : []),
          ...(sent.themeCandidates || []),
        ],
      };
      const analysis = await post<Analysis>("/api/analyze", {
        question: sent.question,
        portfolio,
        new_cash: sent.newCash ?? undefined,
        theme: sent.theme ?? undefined,
        stock: sent.stock ? { position_id: sent.stock } : undefined,
        settings: storedSettings(committedSettings) ?? undefined,
        ...(sent.rebalance
          ? {
              portfolio_review: { prior_theses: [], risk_context: null },
              comparison: portfolioReviewComparison(portfolio),
            }
          : {}),
        ...(sent.theme ? { comparison: themeComparison(portfolio, sent.theme) } : {}),
      });
      if (runId.current !== id) return;
      if (analysis.status !== "completed") throw new Error("The analysis was not completed.");
      setAnswer({ ...sent, analysis, revision });
      setTab(sent.rebalance ? "proposed" : "evidence");
      setFocus(null);
      try {
        localStorage.setItem(ANSWER_KEY, JSON.stringify({ ...sent, analysis, revision }));
      } catch {
        /* memory only */
      }
      const durations = [...pastDurations(sent), Date.now() - startedAt].slice(-3);
      try {
        localStorage.setItem(durationsKey(sent), JSON.stringify(durations));
      } catch {
        /* per-viewer convenience only */
      }
      setPast(durations);
      if (document.hidden) document.title = `Answer ready · ${TITLE}`;
    } catch (error) {
      if (runId.current === id)
        setFailure({
          ...sent,
          message:
            error instanceof Error ? error.message : "The analysis could not be completed.",
        });
    } finally {
      if (runId.current === id) setRun(null);
    }
  }

  /** Stores a captured copy of the draft as an immutable hypothetical portfolio. Nothing is committed. */
  const storeDraft = (portfolio: SavedPortfolio) =>
    createArtifact({
      portfolio,
      label: "Hypothetical Draft Portfolio",
      source_artifact_id: committedRevision ? `art-rev-${committedRevision}` : null,
    });

  /** Explicit hypothetical analysis captures immutable draft state */
  async function analyzeHypotheticalDraft() {
    if (!draftSnapshot) return;
    const capturedSnapshot = structuredClone(draftSnapshot);
    const capturedSettings = structuredClone(draftSettings);
    const capturedBenchmark = structuredClone(draftBenchmark);
    const capturedAverageCosts = structuredClone(draftAverageCosts);
    const capturedUnresolved = structuredClone(draftUnresolved);

    const id = ++runId.current;
    const startedAt = Date.now();
    const currentQ = question.trim() || "Review exposure, concentration and fund overlap";
    const sent: Sent = {
      question: currentQ,
      newCash: null,
      stock: null,
      isHypothetical: true,
      snapshot: capturedSnapshot,
    };
    conversation.close();
    setRun({ ...sent, id, startedAt });
    setNow(startedAt);
    setPast(pastDurations(sent));
    setFailure(null);
    setClarify(null);
    setSaved(null);
    setReopened(null);
    setView("chat");
    setCollapsed(true);
    setNotice("");

    try {
      const candidatePortfolio: SavedPortfolio = {
        snapshot: capturedSnapshot,
        settings: storedSettings(capturedSettings),
        benchmark: capturedBenchmark,
        unresolved: capturedUnresolved,
        average_costs: capturedAverageCosts,
        saved_at: null,
      };

      const artifactRef = await storeDraft(candidatePortfolio);

      sent.artifactReference = artifactRef;

      const dated =
        capturedSnapshot.positions.some(row => row.mark) || capturedSnapshot.fx.length > 0;
      const base = dated
        ? capturedSnapshot
        : { ...capturedSnapshot, as_of: new Date().toLocaleDateString("en-CA") };

      let benchmarkRow: Position | null = null;
      if (capturedBenchmark) {
        const accountId = capturedSnapshot.accounts[0]?.id || "default";
        benchmarkRow = {
          id: BENCHMARK_FUND,
          account_id: accountId,
          kind: "etf",
          etf_role: "diversified",
          currency: capturedBenchmark.currency,
          ticker: capturedBenchmark.ticker,
          listing: capturedBenchmark.listing,
          company_name: capturedBenchmark.name || undefined,
          shares: "0",
        };
      }

      const portfolio = {
        ...base,
        positions: [
          ...base.positions,
          ...(benchmarkRow ? [benchmarkRow] : []),
        ],
      };

      const analysis = await post<Analysis>("/api/analyze", {
        question: sent.question,
        portfolio,
        settings: storedSettings(capturedSettings) ?? undefined,
        artifact_reference: artifactRef,
      });

      if (runId.current !== id) return;
      if (analysis.status !== "completed") throw new Error("The analysis was not completed.");

      const resultWithRef: Analysis = {
        ...analysis,
        artifact_reference: artifactRef,
      };

      setAnswer({ ...sent, analysis: resultWithRef });
      setTab("evidence");
      setFocus(null);
      try {
        localStorage.setItem(ANSWER_KEY, JSON.stringify({ ...sent, analysis: resultWithRef }));
      } catch {
        /* memory only */
      }
      const durations = [...pastDurations(sent), Date.now() - startedAt].slice(-3);
      try {
        localStorage.setItem(durationsKey(sent), JSON.stringify(durations));
      } catch {
        /* per-viewer convenience only */
      }
      setPast(durations);
      if (document.hidden) document.title = `Answer ready · ${TITLE}`;
    } catch (error) {
      if (runId.current === id)
        setFailure({
          ...sent,
          message:
            error instanceof Error
              ? error.message
              : "The model service could not complete the review. Try again.",
        });
    } finally {
      if (runId.current === id) setRun(null);
    }
  }

  async function handleCandidate(question: string, ticker: string, listing?: string) {
    setNotice("");
    try {
      const res = await post<CandidateResult>("/api/candidates", { ticker, listing });
      if (res.is_fund) {
        setClarify({
          kind: "clarify",
          question,
          options: [],
          review: true,
          message: `${ticker.toUpperCase()} is a fund, not a single company. Stock Analysis covers individual US and Canadian stocks; a portfolio review covers your funds.`,
        });
        setReopened(null);
        setCollapsed(false);
        return;
      }
      if (res.unresolved?.reason === "multiple") {
        setClarify({
          kind: "clarify",
          question,
          options: [],
          review: false,
          candidateListings: res.unresolved.listings,
          message: `"${ticker}" matches more than one listing. Which one did you mean?`,
        });
        setReopened(null);
        setCollapsed(false);
        return;
      }
      if (res.unresolved?.reason === "not_found" || !res.position) {
        setClarify({
          kind: "clarify",
          question,
          options: (committedSnapshot?.positions || []).filter(isSupportedStock),
          review: true,
          message: `I couldn't find "${ticker}" as a US or Canadian stock. Which holding did you mean?`,
        });
        setReopened(null);
        setCollapsed(false);
        return;
      }
      void analyze({ question, newCash: null, stock: res.position.id, candidate: res.position });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Candidate lookup failed.");
    }
  }

  async function deleteDecision(decision: SavedDecision) {
    if (!window.confirm(`Delete "${decision.question}"? This can't be undone.`)) return;
    try {
      await del(`/api/decisions/${encodeURIComponent(decision.id)}`);
      setDecisions(list => list.filter(d => d.id !== decision.id));
      setSavedResults(map => {
        const next = { ...map };
        for (const [k, v] of Object.entries(next)) {
          if (v === decision.id) delete next[k];
        }
        return next;
      });
      if (reopened?.id === decision.id) setReopened(null);
      if (saved?.id === decision.id) setSaved(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The decision couldn't be deleted.");
    }
  }

  async function clearDecisions() {
    if (!window.confirm(`Delete all ${decisions.length} saved decisions? This can't be undone.`))
      return;
    try {
      await del("/api/decisions");
      setDecisions([]);
      setSavedResults({});
      setReopened(null);
      setSaved(null);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Decisions couldn't be cleared.");
    }
  }

  function updateTheme(next: ThemeInput | ((prev: ThemeInput) => ThemeInput)) {
    setTheme(prev => {
      const updated = typeof next === "function" ? next(prev) : next;
      setAnswer(curr => {
        if (!curr?.analysis.theme) return curr;
        return {
          ...curr,
          analysis: {
            ...curr.analysis,
            theme: {
              ...curr.analysis.theme,
              status: "awaiting_agreement",
              context: updated,
            },
          },
        };
      });
      return updated;
    });
  }

  async function addThemeCandidate(ticker: string): Promise<boolean> {
    try {
      const res = await post<CandidateResult>("/api/candidates", { ticker });
      if (res.unresolved || !res.position) return false;
      const pos = res.position;
      setThemeCandidates(prev => [...prev.filter(p => p.id !== pos.id), pos]);
      updateTheme(prev => {
        if (prev.shortlist.includes(pos.id)) return prev;
        const nextList = [...prev.shortlist, pos.id].slice(0, 4);
        return {
          ...prev,
          shortlist: nextList,
          max_candidates: Math.max(1, nextList.length),
          confirmed: false,
        };
      });
      return true;
    } catch {
      return false;
    }
  }

  function removeThemeCandidate(id: string) {
    setThemeCandidates(prev => prev.filter(p => p.id !== id));
    updateTheme(prev => {
      const nextList = prev.shortlist.filter(item => item !== id);
      return {
        ...prev,
        shortlist: nextList,
        max_candidates: Math.max(1, nextList.length),
        confirmed: false,
      };
    });
  }

  function agreeTheme() {
    if (!answer?.analysis.theme) return;
    const themeCtx = answer.analysis.theme.context;
    const agreed: ThemeInput = { ...themeCtx, confirmed: true };
    setTheme(agreed);
    void analyze({
      question: answer.question,
      newCash: null,
      theme: agreed,
      themeCandidates,
    });
  }

  /** Leaves any workflow view so the thread is what the centre shows. */
  function showConversation() {
    runId.current++;
    setRun(null);
    setFailure(null);
    setClarify(null);
    setReopened(null);
    setView("chat");
    setWorkflow(null);
    setCollapsed(false);
    setNotice("");
    setRailOpen(false);
  }

  /** Portfolio or Decisions in place of the conversation. What the conversation showed is kept for the way back. */
  function openView(next: "portfolio" | "decisions") {
    setView(next);
    setPanelOpen(false);
    setRailOpen(false);
    setFocus(null);
    requestAnimationFrame(() => document.getElementById(`${next}-title`)?.focus());
  }

  function backToChat() {
    setView("chat");
    requestAnimationFrame(() => document.getElementById("ask")?.focus());
  }

  /** A saved decision, as it was saved, with its evidence in the details. */
  function openDecision(decision: SavedDecision) {
    setView("chat");
    setReopened(decision);
    setClarify(null);
    setTab("evidence");
    setFocus(null);
    setRailOpen(false);
  }

  function openThread(id: string) {
    showConversation();
    conversation.open(id);
  }

  function chat(text: string) {
    // A question asked from another view (the register, a workflow answer) starts a new conversation
    // rather than landing in a thread that is not on screen.
    // Asked from the new conversation already on screen, it stays in that same view.
    if (!showThread) conversation.close(viewOpen || !!reopened || !!run || !!clarify || !!failure || !!answer);
    showConversation();
    void sendToConversation(text.trim());
  }

  /** Sends into the conversation. With the draft selected, the draft is first stored as an immutable
   *  hypothetical portfolio and only that stored copy is analyzed: later edits never reach the run, and
   *  without the selection no draft state is sent at all. */
  function sendToConversation(text: string, workflowInput?: Record<string, unknown>) {
    if (!askingDraft) return void conversation.send(text, workflowInput);
    const portfolio: SavedPortfolio = structuredClone({
      snapshot: draftSnapshot,
      settings: storedSettings(draftSettings),
      benchmark: draftBenchmark,
      unresolved: draftUnresolved,
      average_costs: draftAverageCosts,
      saved_at: null,
    });
    // The selection is used up here, where it was made: a selection made later, in whatever view is then
    // open, is a different one and is left alone when this capture finishes.
    setUseDraft(false);
    // Fixed before the draft is stored: the question goes where it was asked, whatever is opened meanwhile.
    void askAboutDraft({ id: crypto.randomUUID(), view: conversation.currentView(), destination: conversation.destination(), text, workflowInput, portfolio });
  }

  /** Stores the captured draft, then sends the question about it. A failure keeps the whole question, with the
   *  draft as it was captured, so it can be sent again from the view it was asked in. */
  async function askAboutDraft(asked: DraftQuestion) {
    setCapturing(views => [...views, asked.view]);
    try {
      const captured = await storeDraft(asked.portfolio);
      const started = await conversation.send(asked.text, asked.workflowInput, captured.id, { threadId: asked.destination, view: asked.view });
      // Another message was still being delivered, so this one was not started.
      if (!started) throw new Error("not sent");
      forgetFailed(asked.id);
    } catch {
      // ponytail: one failed draft question is kept per view; a second failure there replaces the first.
      setCaptureFailed(failed => ({ ...failed, [asked.view]: asked }));
    } finally {
      setCapturing(views => views.filter(view => view !== asked.view));
    }
  }

  /** Failed draft questions whose conversation is not on screen and is not listed: a new conversation, or a
   *  thread with no accepted question yet. The rail lists the question itself as the way back to it. */
  const unsentElsewhere = Object.values(captureFailed).filter(
    asked => asked.view !== conversation.viewKey && !conversation.threads?.some(item => item.id === asked.destination)
  );

  function ask(text: string) {
    // With the draft selected, any question that is not a slash shortcut goes to the conversation as a hypothetical.
    if (askingDraft ? !text.trim().startsWith("/") : isConversation(text, committedSnapshot)) {
      chat(text);
      return;
    }
    const next = route(text, committedSnapshot);
    if (next.kind === "clarify") {
      setClarify(next);
      setReopened(null);
      setCollapsed(false);
      return;
    }
    if (next.kind === "candidate") {
      void handleCandidate(next.question, next.ticker);
      return;
    }
    if (next.kind === "theme") {
      setWorkflow("theme");
      if (next.name) updateTheme(t => ({ ...t, name: next.name || t.name, confirmed: false }));
      setQuestion(next.question);
      setCollapsed(false);
      return;
    }
    if (next.kind === "new-cash") {
      setWorkflow("new-cash");
      setNewCash(prev => ({
        ...prev,
        amount: next.amount || null,
        currency: next.currency || prev.currency || committedSnapshot?.reporting_currency || null,
        confirmed: false,
      }));
      setQuestion(next.question);
      setCollapsed(false);
      return;
    }
    void analyze({
      question: next.question,
      newCash: null,
      stock: next.kind === "stock" ? next.position.id : null,
      rebalance: next.kind === "rebalance",
    });
  }
  const analyzeStock = (position: Position, question: string) =>
    void analyze({ question, newCash: null, stock: position.id });

  function offerStock(position: Position) {
    setWorkflow(null);
    setQuestion(`/stock ${position.ticker}`);
    setRailOpen(false);
    setReopened(null);
    setView("chat");
    setCollapsed(false);
    setTimeout(() => document.getElementById("ask")?.focus(), 0);
  }

  function stopWaiting() {
    runId.current++;
    setRun(null);
    setCollapsed(false);
  }

  /** The register entry for a result: its workflow and subject, from what was sent when known, else from the stored result itself. */
  function decisionFor(
    result: Analysis,
    from: Sent | null,
    snapshot: Snapshot | null = committedSnapshot
  ): { workflow: DecisionWorkflow; subject: DecisionSubject } {
    // A desk answer keeps its long-standing precedence; a stored result has only itself to go by.
    const workflow: DecisionWorkflow = !from
      ? workflowOf(result)
      : from.stock || result.stock
        ? "stock"
        : from.newCash || result.allocation
          ? "new_cash"
          : from.theme || result.theme
            ? "theme"
            : from.rebalance || result.reunderwriting
              ? "rebalance"
              : "review";
    if (workflow === "stock") {
      const id = from?.stock || result.stock?.position_id;
      const held = result.portfolio.positions.find(row => row.supplied.id === id)?.supplied;
      // A stored result is saved as the holding it analyzed: today's portfolio may hold that position ID under
      // another ticker or listing. Only a desk answer, which has no stored source, falls back to it.
      const current = from ? committedSnapshot?.positions.find(row => row.id === id) : undefined;
      const ticker = (from === sent ? sentStock : null) || held?.ticker || (from ? tickerOf(id) : null) || "Stock";
      const listing = from?.candidate?.listing || current?.listing || held?.listing || null;
      return { workflow, subject: { label: ticker, ticker, listing } };
    }
    if (workflow === "new_cash") {
      const cashInput = from?.newCash || result.allocation?.context || EMPTY_CASH;
      return {
        workflow,
        subject: {
          label: newCashLabel(cashInput, snapshot) || "New cash",
          new_cash: {
            amount: cashInput.amount ?? null,
            account_id: cashInput.account_id ?? null,
            currency: cashInput.currency ?? null,
            cash_position_id: cashInput.cash_position_id ?? null,
          },
        },
      };
    }
    if (workflow === "theme") {
      const th = from?.theme || result.theme?.context || EMPTY_THEME;
      return {
        workflow,
        subject: {
          label: th.name || "Theme discovery",
          theme: { name: th.name || null, mechanism: th.mechanism || null, shortlist: th.shortlist || [] },
        },
      };
    }
    return { workflow, subject: { label: workflow === "rebalance" ? "Rebalance" : "Portfolio review" } };
  }

  async function save() {
    if (!result) return;
    setSaving(true);
    setNotice("");
    try {
      const record = await post<SavedDecision>("/api/decisions", { result, ...decisionFor(result, sent) });
      setSaved(record);
      setDecisions(list => [record, ...list.filter(d => d.id !== record.id)]);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "The decision couldn't be saved.");
    } finally {
      setSaving(false);
    }
  }

  /** Saves a decision from a result stored in the conversation. The stored values are saved as they are. */
  async function saveResult(artifactId: string, analysis: Analysis, source: Snapshot) {
    setSavingResult(artifactId);
    setSaveFailed(null);
    try {
      const record = await post<SavedDecision>("/api/decisions", {
        result: analysis,
        artifact_id: artifactId,
        ...decisionFor(analysis, null, source),
      });
      setSavedResults(map => ({ ...map, [artifactId]: record.id }));
      setDecisions(list => [record, ...list.filter(d => d.id !== record.id)]);
    } catch {
      setSaveFailed(artifactId);
    } finally {
      setSavingResult(null);
    }
  }

  async function confirm(decision: SavedDecision, action: DecisionAction, notes: string | undefined) {
    const updated = await post<SavedDecision>(`/api/decisions/${decision.id}/confirm`, {
      action,
      notes,
    });
    setDecisions(list => list.map(d => (d.id === updated.id ? updated : d)));
    if (saved?.id === updated.id) setSaved(updated);
    if (reopened?.id === updated.id) setReopened(updated);
  }

  function startOver() {
    runId.current++;
    setRun(null);
    setAnswer(null);
    setFailure(null);
    setClarify(null);
    setSaved(null);
    setReopened(null);
    setView("chat");
    conversation.close();
    try {
      localStorage.removeItem(ANSWER_KEY);
    } catch {
      /* memory only */
    }
    setWorkflow(null);
    setQuestion("");
    setTheme(EMPTY_THEME);
    setThemeCandidates([]);
    setCollapsed(false);
    setTab("holdings");
    setRailOpen(false);
  }

  function rerunDecision(decision: SavedDecision) {
    setReopened(null);
    setView("chat");

    if (decision.workflow === "review") {
      setWorkflow(null);
      void analyze({ question: decision.question, newCash: null, stock: null });
    } else if (decision.workflow === "rebalance") {
      setWorkflow(null);
      void analyze({ question: decision.question, newCash: null, stock: null, rebalance: true });
    } else if (decision.workflow === "stock") {
      setWorkflow(null);
      const ticker = decision.subject?.ticker || decision.subject?.label;
      const listing = decision.subject?.listing ?? undefined;
      const sameTicker = (committedSnapshot?.positions ?? []).filter(
        row => isSupportedStock(row) && !!ticker && row.ticker?.toUpperCase() === ticker.toUpperCase()
      );
      // A re-run never moves to another listing of the same ticker. A decision that recorded its listing runs
      // on a holding with that listing; one that recorded none runs on a holding only when a single listing is
      // held. Anything else goes to the lookup below, which analyzes the recorded listing or asks which one.
      const held = listing
        ? sameTicker.find(row => row.listing === listing)
        : new Set(sameTicker.map(row => row.listing)).size === 1
          ? sameTicker[0]
          : undefined;
      if (held) {
        void analyze({ question: decision.question, newCash: null, stock: held.id });
      } else if (ticker) {
        void handleCandidate(decision.question, ticker, listing);
      } else {
        setQuestion(decision.question);
        setCollapsed(false);
      }
    } else if (decision.workflow === "new_cash") {
      setWorkflow("new-cash");
      setQuestion(decision.question);
      setNewCash({
        amount: decision.subject?.new_cash?.amount ?? null,
        account_id: decision.subject?.new_cash?.account_id ?? null,
        currency: (decision.subject?.new_cash?.currency as any) ?? null,
        cash_position_id: decision.subject?.new_cash?.cash_position_id ?? null,
        confirmed: false,
        risk_context: null,
      });
      setCollapsed(false);
      setTab("holdings");
    } else if (decision.workflow === "theme") {
      setWorkflow("theme");
      setQuestion(decision.question);
      setTheme({
        name: decision.subject?.theme?.name || "",
        mechanism: decision.subject?.theme?.mechanism || "",
        shortlist: decision.subject?.theme?.shortlist || [],
        max_candidates: 2,
        max_tool_calls: 16,
        confirmed: false,
        risk_context: null,
      });
      setCollapsed(false);
      setTab("scenarios");
    } else {
      setWorkflow(null);
      setQuestion(decision.question);
      setCollapsed(false);
      setTimeout(() => document.getElementById("ask")?.focus(), 0);
    }
  }

  const sent = run ?? failure ?? answer;
  const tickerOf = (id: string | null | undefined, cand?: Position | null) =>
    id
      ? (cand && cand.id === id ? cand.ticker : null) ||
        (id === BENCHMARK_FUND ? committedBenchmark?.ticker : null) ||
        committedSnapshot?.positions.find(row => row.id === id)?.ticker ||
        answer?.analysis.portfolio.positions.find(row => row.supplied.id === id)?.supplied
          .ticker ||
        id
      : null;
  const sentStock = tickerOf(sent?.stock, sent?.candidate);
  const sentLabel = sent?.newCash && newCashLabel(sent.newCash, committedSnapshot);
  const ctxLabel = reopened
      ? `Decision · ${shortDate(reopened.saved_at)}`
      : showThread
        ? conversation.thread?.messages.find(message => message.role === "user")?.text || "Conversation"
        : sentLabel
        ? `New cash · ${sentLabel}`
        : sent
          ? sent.newCash
            ? "New cash"
            : sent.theme
              ? `Theme · ${sent.theme.name || "discovery"}`
              : sentStock
                ? `Stock analysis · ${sentStock}`
                : sent.rebalance
                  ? "Rebalance"
                  : "Portfolio review"
          : workflow === "theme"
            ? "Theme discovery"
            : workflow
              ? "New cash"
              : "New conversation";
  const subjectOf = (analysis: Analysis) =>
    analysis.stock ? analysis.portfolio.positions.find(row => row.supplied.id === analysis.stock!.position_id)?.supplied.ticker : null;
  const panelTitle = reopened
    ? decisionLabel(reopened)
    : result
      ? `${WORKFLOW_TITLE[workflowOf(result)]}${subjectOf(result) ? ` · ${subjectOf(result)}` : ""}`
      : "Your portfolio";
  const panelSubtitle = reopened
    ? `Saved ${shortDate(reopened.saved_at)} · as it was then`
    : result
      ? `${(showThread ? threadResult?.revision : result.artifact_reference?.revision) ? `Portfolio revision ${showThread ? threadResult?.revision : result.artifact_reference?.revision}` : `Portfolio as of ${fullDate(result.portfolio.holdings_as_of || result.portfolio.as_of)}`} · valued ${shortDate(result.portfolio.reviewed_at)}`
      : committedSnapshot
        ? `Portfolio as of ${fullDate(committedSnapshot.as_of)}`
        : "No portfolio saved yet";
  const holdingsCount =
    committedSnapshot?.positions.filter(row => row.kind !== "cash").length ?? 0;
  const hasFund =
    committedSnapshot?.positions.some(
      row => row.kind === "etf" && row.etf_role === "diversified"
    ) || !!committedBenchmark;
  const portfolioLine = committedSnapshot
    ? `${holdingsCount} holding${holdingsCount === 1 ? "" : "s"}${
        committedSnapshot.positions.length > holdingsCount
          ? ` and ${committedSnapshot.positions.length - holdingsCount} cash balance${
              committedSnapshot.positions.length - holdingsCount === 1 ? "" : "s"
            }`
          : ""
      } in ${committedSnapshot.accounts.length} account${
        committedSnapshot.accounts.length === 1 ? "" : "s"
      }, your portfolio as of ${fullDate(committedSnapshot.as_of)}`
    : "";
  const rulesLine =
    committedSettings.single_company_cap || committedSettings.active_budget
      ? `Your rules: ${
          committedSettings.single_company_cap
            ? `${pct(committedSettings.single_company_cap)} per company`
            : "no company cap"
        }, ${
          committedSettings.active_budget
            ? `${pct(committedSettings.active_budget)} in active picks`
            : "no active budget"
        }`
      : "No rules set, so no limits are checked";
  const scope = !committedSnapshot
    ? []
    : run?.stock
      ? [
          `${sentStock}'s latest filings and issuer material, with their dates`,
          "Downside, base and upside cases calculated in Python from the reported facts",
          `Its value and weight in your portfolio as of ${fullDate(
            committedSnapshot.as_of
          )}, against a fund, cash and doing nothing`,
          rulesLine,
        ]
      : run?.rebalance
        ? [
            `${portfolioLine}, valued with dated prices and exchange rates`,
            "Each company you own re-checked against its filings on hand, in one run rather than one analysis per holding",
            `Reducing, keeping or adding, against ${hasFund ? "a diversified fund, " : ""}cash and changing nothing`,
            rulesLine,
          ]
        : run?.newCash
          ? [
              `${portfolioLine}, plus the new cash`,
              rulesLine,
              "A bounded screen of what you could add to, then filings for candidates that could change the answer",
              `Against ${hasFund ? "a diversified fund, " : ""}keeping cash and doing nothing`,
            ]
          : run?.theme
            ? [
                `Theme: ${run.theme.name || "Idea"}`,
                `Mechanism: ${run.theme.mechanism}`,
                `Agreed shortlist: ${run.theme.shortlist.map(id => tickerOf(id) || id).join(", ")}`,
                `Effort limit: ${run.theme.max_tool_calls} research calls`,
              ]
            : [
                `${portfolioLine}, valued with dated prices and exchange rates`,
                "Direct company exposure across accounts, and what your funds hold where that's known",
                rulesLine,
              ];
  const review = (question: string) => void analyze({ question, newCash: null, stock: null });

  /** The result memo for one analysis, by what the backend computed. The desk's answer view and a stored
   *  result in a conversation draw the same memo. */
  function memo(
    analysis: Analysis,
    o: {
      cite: typeof cite;
      saved: SavedDecision | null;
      onTab: (next: Tab) => void;
      snapshot: Snapshot | null;
      cash?: NewCashInput | null;
      onAgree?: () => void;
      onRerun: (next: NewCashInput) => void;
    }
  ) {
    const key = analysis.portfolio.reviewed_at;
    const shared = {
      result: analysis,
      snapshot: o.snapshot,
      cite: o.cite,
      saved: o.saved,
      onTab: o.onTab,
      onConfirm: (action: DecisionAction, notes: string | undefined) =>
        o.saved ? confirm(o.saved, action, notes) : Promise.resolve(),
    };
    const workflow = workflowOf(analysis);
    if (workflow === "rebalance") return <RebalanceAnswer key={key} {...shared} />;
    if (workflow === "stock") return <StockAnswer key={key} {...shared} />;
    if (workflow === "theme") return <ThemeAnswer key={key} {...shared} onAgree={o.onAgree} />;
    if (workflow === "review") return <ReviewAnswer key={key} {...shared} />;
    const cash = o.cash ?? analysis.allocation!.context;
    return (
      <Answer
        key={key}
        {...shared}
        sent={{ question: analysis.question, newCash: cash }}
        onRerun={risk => o.onRerun({ ...cash, risk_context: risk })}
      />
    );
  }

  /** A stored analysis inside the conversation: the same memo, reading the stored values, with its own save control. */
  function storedResult(analysis: Analysis, reference: ArtifactReference, source: Snapshot) {
    const open = () => setThreadResult({ id: reference.id, analysis, revision: reference.revision, basis: reference.basis });
    const savedHere = decisions.find(decision => decision.id === savedResults[reference.id]) ?? null;
    return (
      <>
        {memo(analysis, {
          cite: {
            numbers: evidenceFor(analysis, null).numbers,
            onCite: id => {
              open();
              cite.onCite(id);
            },
          },
          saved: savedHere,
          onTab: next => {
            open();
            openTab(next);
          },
          snapshot: source,
          // A stored result never carries its confirmation forward: the form opens for a fresh one.
          onRerun: next => {
            setNewCash({ ...next, confirmed: false });
            setQuestion(analysis.question);
            openClarificationForm("/new-cash");
          },
        })}
        <div className={threadStyles.save}>
          <Button
            variant="ghost"
            onClick={() => {
              open();
              openTab(analysis.reunderwriting ? "proposed" : "evidence");
            }}
          >
            Open details
          </Button>
          {savedHere ? (
            <span role="status">Saved to Decisions {clock(savedHere.saved_at)}</span>
          ) : (
            <Button
              variant="secondary"
              loading={savingResult === reference.id}
              onClick={() => void saveResult(reference.id, analysis, source)}
            >
              Save decision
            </Button>
          )}
          {saveFailed === reference.id && (
            <span role="alert">The decision was not saved. Check that the analyst service is running, then save it again.</span>
          )}
        </div>
      </>
    );
  }

  /** An explicit form submission into the open conversation. It starts a new run; it never edits an earlier one. */
  function submitWorkflow(text: string, workflowInput: Record<string, unknown>) {
    setWorkflow(null);
    void sendToConversation(text.trim(), workflowInput);
  }

  function openClarificationForm(form: ClarificationForm) {
    if (form === "holdings") return openTab("holdings");
    setResuming(true);
    setWorkflow(form === "/theme" ? "theme" : "new-cash");
    if (form === "/new-cash") setQuestion(current => current || "Where should this new cash go?");
    setCollapsed(false);
    setTimeout(() => document.getElementById("ask")?.focus(), 0);
  }

  const activeSnapshot = answer?.snapshot || committedSnapshot;
  // Shown wherever a question can be asked, so the draft is only ever analyzed by explicit choice.
  const capturingHere = capturing.includes(conversation.viewKey);
  const captureFailedHere = captureFailed[conversation.viewKey] ?? null;
  const draftBasis = (captureFailedHere || isDirty) && (
    <div className={threadStyles.draft}>
      {captureFailedHere && (
        <div className={threadStyles.notice}>
          <Alert tone="danger" title="Your question was not sent">
            {`"${captureFailedHere.text}" was asked about your unsaved draft and could not be sent. Check that the analyst service is running, then send the question again.`}
          </Alert>
          <div className={threadStyles.actions}>
            <Button variant="secondary" disabled={capturingHere || conversation.busy} onClick={() => void askAboutDraft(captureFailedHere)}>
              Send question again
            </Button>
            <Button variant="ghost" onClick={() => forgetFailed(captureFailedHere.id)}>
              Discard question
            </Button>
          </div>
        </div>
      )}
      {isDirty && (
        <div ref={draftNote} data-testid="draft-note" className={threadStyles.draftNote}>
          <p className="cap">
            Answers use your saved portfolio. Unsaved draft edits are not included until you review and confirm them.
          </p>
          <Checkbox
            label="Ask about my unsaved draft"
            description="Your next question uses the draft as a hypothetical portfolio. Your saved portfolio stays as it is, and slash workflows still use it."
            checked={useDraft}
            disabled={conversation.busy || capturingHere}
            onCheckedChange={next => setUseDraft(next === true)}
          />
        </div>
      )}
    </div>
  );
  let center;
  if (reopened)
    center = (
      <Historical
        decision={reopened}
        snapshot={committedSnapshot}
        cite={cite}
        onConfirm={(action, notes) => confirm(reopened, action, notes)}
        onRerun={() => rerunDecision(reopened)}
      />
    );
  else if (run)
    center = (
      <>
        <Echo
          question={run.question}
          newCash={run.newCash}
          snapshot={committedSnapshot}
          stock={sentStock}
          rebalance={run.rebalance}
          theme={run.theme}
        />
        <Waiting
          title={
            run.newCash
              ? "Analyzing your new cash."
              : run.theme
                ? `Researching theme ${run.theme.name || ""}.`
                : run.stock
                  ? `Analyzing ${sentStock}.`
                  : run.rebalance
                    ? "Checking whether anything should change."
                    : "Reviewing your portfolio."
          }
          startedAt={run.startedAt}
          now={now}
          past={past}
          scope={scope}
          onStop={stopWaiting}
        />
      </>
    );
  else if (clarify)
    center = (
      <Clarify
        {...clarify}
        onStock={position => analyzeStock(position, clarify.question)}
        onReview={() =>
          review(clarify.question.startsWith("/") ? "Review my portfolio" : clarify.question)
        }
        candidateListings={clarify.candidateListings}
        onSelectListing={listing =>
          void handleCandidate(clarify.question, listing.ticker, listing.listing ?? undefined)
        }
      />
    );
  else if (failure)
    center = (
      <>
        <Echo
          question={failure.question}
          newCash={failure.newCash}
          snapshot={committedSnapshot}
          stock={sentStock}
          rebalance={failure.rebalance}
          theme={failure.theme}
        />
        <Failure message={failure.message} onRetry={() => analyze(failure)} />
      </>
    );
  else if (showThread)
    center = (
      <>
        <Thread
          thread={conversation.thread}
          pending={conversation.pending}
          sendError={conversation.sendError}
          loadError={conversation.loadError}
          onRetrySend={() => void conversation.retry()}
          onDiscardSend={() => {
            conversation.discardPending();
            document.getElementById("ask")?.focus();
          }}
          onAskAgain={chat}
          onReload={() => void conversation.reload()}
          onStop={() => void conversation.stop()}
          stopping={conversation.stopping}
          stopError={conversation.stopError}
          snapshot={committedSnapshot}
          committedRevision={committedRevision}
          proposals={proposalsLoaded ? proposals : null}
          proposalsFailed={proposalsFailed}
          onReloadProposals={refreshProposals}
          theme={theme}
          busy={conversation.busy}
          renderResult={storedResult}
          onReviewProposal={openProposal}
          onOpenForm={openClarificationForm}
          onSubmitWorkflow={submitWorkflow}
        />
        {draftBasis}
        <div ref={threadEnd} />
      </>
    );
  else if (answer)
    center = memo(answer.analysis, {
      cite,
      saved,
      onTab: openTab,
      snapshot: activeSnapshot,
      cash: answer.newCash,
      onAgree: agreeTheme,
      onRerun: next => {
        setNewCash(next);
        void analyze({ question: answer.question, newCash: next });
      },
    });
  else if (restoring)
    center = (
      <p className="cap" role="status">
        Loading your saved portfolio…
      </p>
    );
  else if (!committedSnapshot)
    center = (
      <div className="hero">
        <h1>Start with what you own.</h1>
        <p className="hero-sub">
          Import your holdings once. They&apos;re saved on this computer and loaded every time.
          Prices, values and weights come from the analysis, not from your cost. You can ask a general
          question below before importing anything.
        </p>
        <ImportCsv
          snapshot={null}
          baseRevision={committedRevision}
          onProposal={handleProposal}
          onError={setNotice}
          label="Choose a holdings CSV"
        />
      </div>
    );
  else
    center = (
      <div className="hero">
        <h1>
          {workflow === "theme"
            ? "What theme should we explore?"
            : workflow === "new-cash"
              ? "Where should new money go?"
              : "What do you want to look at today?"}
        </h1>
        <p className="hero-sub">
          {workflow === "theme"
            ? "Frame an investment theme, name candidate stocks or let the analyst propose them, and test whether exposure makes sense against your portfolio."
            : workflow === "new-cash"
              ? "The analyst screens what you could add to, researches candidates that could change the answer, and compares them with keeping cash and doing nothing. You place any order yourself."
              : "Ask about your portfolio, a company or an idea."}
        </p>
      </div>
    );

  // The home screen: no answer, conversation or decision is open, so the composer sits in the middle of the page.
  const home =
    !viewOpen && !reopened && !run && !clarify && !failure && !showThread && !answer && !restoring;
  const blockedByIdentity = committedUnresolved.length > 0;
  const rulesSet = [
    committedSettings.single_company_cap && `Cap ${pct(committedSettings.single_company_cap)}`,
    committedSettings.active_budget && `active ${pct(committedSettings.active_budget)}`,
  ].filter(Boolean);
  const waitingOn = [
    pendingProposals.length > 0 && `${pendingProposals.length} change${pendingProposals.length === 1 ? "" : "s"}`,
    blockedByIdentity && `${committedUnresolved.length} holding${committedUnresolved.length === 1 ? "" : "s"} to identify`,
  ].filter(Boolean);
  const homeBelow = home && committedSnapshot && !workflow && (
    <div className="home-below">
      <div className="col">
        <div className="starters">
          <button
            type="button"
            className="starter"
            disabled={blockedByIdentity}
            onClick={() => {
              setQuestion("Review my portfolio");
              review("Review my portfolio");
            }}
          >
            <span className="ico" aria-hidden="true"><ChartPie size={16} /></span>
            <b>Review my portfolio</b>
            <span className="m">Exposure and overlap</span>
          </button>
          <button
            type="button"
            className="starter"
            disabled={blockedByIdentity}
            onClick={() => {
              setWorkflow(null);
              setQuestion("/stock ");
              document.getElementById("ask")?.focus();
            }}
          >
            <span className="ico blue" aria-hidden="true"><TrendingUp size={16} /></span>
            <b>Analyze a stock</b>
            <span className="m">One company you own</span>
          </button>
          <button
            type="button"
            className="starter"
            onClick={() => {
              setWorkflow("new-cash");
              setCollapsed(false);
              document.getElementById("ask")?.focus();
            }}
          >
            <span className="ico sand" aria-hidden="true"><Banknote size={16} /></span>
            <b>Place new cash</b>
            <span className="m">Where new money goes</span>
          </button>
          <button
            type="button"
            className="starter"
            disabled={blockedByIdentity}
            onClick={() =>
              void analyze({
                question: "Should I rebalance my portfolio?",
                newCash: null,
                rebalance: true,
              })
            }
          >
            <span className="ico sand" aria-hidden="true"><Scale size={16} /></span>
            <b>Rebalance</b>
            <span className="m">Reduce, keep or add</span>
          </button>
          <button
            type="button"
            className="starter"
            disabled={blockedByIdentity}
            onClick={() => {
              setWorkflow("theme");
              setCollapsed(false);
              document.getElementById("ask")?.focus();
            }}
          >
            <span className="ico blue" aria-hidden="true"><Lightbulb size={16} /></span>
            <b>Test a theme</b>
            <span className="m">An idea and a short list</span>
          </button>
        </div>
        <dl className="glance">
          <div>
            <dt>Your portfolio{committedRevision != null ? ` · revision ${committedRevision}` : ""}</dt>
            <dd className="n">
              {committedSnapshot.positions.length} holding{committedSnapshot.positions.length === 1 ? "" : "s"} ·{" "}
              {committedSnapshot.accounts.length} account{committedSnapshot.accounts.length === 1 ? "" : "s"}
            </dd>
          </div>
          <div>
            <dt>Your rules</dt>
            <dd className="n">{rulesSet.length ? rulesSet.join(" · ") : "No rules set"}</dd>
          </div>
          <div>
            <dt>Waiting on you</dt>
            <dd className={waitingOn.length ? "amber" : undefined}>
              {waitingOn.length ? waitingOn.join(" · ") : "Nothing right now"}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );

  const menuBtn = (
    <button
      type="button"
      className="icon-btn menu-btn"
      ref={menuButton}
      aria-label="Open portfolio and decisions"
      onClick={() => {
        setRailOpen(true);
        requestAnimationFrame(() => document.querySelector<HTMLElement>("#rail .rail-close")?.focus());
      }}
    >
      <Menu size={18} aria-hidden="true" />
    </button>
  );

  return (
    <div
      className={`desk${panelOpen ? " panel-open" : ""}${railOpen ? " rail-open" : ""}`}
    >
      <a className="skip" href="#details">
        Skip to details
      </a>
      <Rail
        snapshot={committedSnapshot}
        revision={committedRevision}
        waiting={committedUnresolved.length + pendingProposals.length}
        unconfirmed={decisions.filter(decision => !decision.confirmed_action).length}
        view={view}
        onClose={closeRail}
        inert={sheet}
        threads={conversation.threads}
        threadsError={conversation.threadsError}
        threadId={showThread ? conversation.openId : null}
        onThread={openThread}
        unsent={unsentElsewhere.map(asked => ({ key: asked.id, text: asked.text }))}
        onUnsent={id => {
          const asked = unsentElsewhere.find(item => item.id === id);
          if (!asked) return;
          // Back to the conversation as it was, not to a workflow answer shown since.
          setAnswer(null);
          if (asked.destination) openThread(asked.destination);
          else {
            showConversation();
            conversation.reopenNew(asked.view);
          }
        }}
        running={
          run
            ? run.newCash
              ? "New cash"
              : run.theme
                ? `Theme · ${run.theme.name || "discovery"}`
                : run.stock
                  ? `Stock analysis · ${sentStock}`
                  : run.rebalance
                    ? "Rebalance"
                    : "Portfolio review"
            : null
        }
        onNew={startOver}
        onPortfolio={() => openView("portfolio")}
        onDecisions={() => openView("decisions")}
      />
      <main className={`center${home ? " home" : ""}${view !== "chat" ? " viewing" : ""}`} inert={drawer || sheet}>
        <Connection />
        {view === "portfolio" ? (
          <PortfolioView
            menu={menuBtn}
            restoring={restoring}
            notice={notice}
            revision={committedRevision}
            committed={committedPortfolio}
            unresolved={committedUnresolved}
            draft={draftSnapshot}
            setDraft={setDraftSnapshot}
            settings={draftSettings}
            setSettings={setDraftSettings}
            benchmark={draftBenchmark}
            setBenchmark={setDraftBenchmark}
            valuation={valuation}
            prices={prices}
            refreshing={refreshing}
            onRefreshPrices={() => void refreshPrices(true)}
            isDirty={isDirty}
            edits={edits}
            reviewing={reviewing}
            running={!!run}
            onReviewChanges={reviewChanges}
            onDiscardDraft={discardDraft}
            onAnalyzeDraft={() => void analyzeHypotheticalDraft()}
            pending={pendingProposals}
            onReopenProposal={openProposal}
            onCancelProposal={id => void cancelPendingProposal(id)}
            cancelFailure={cancelFailure}
            onProposal={handleProposal}
            onError={setNotice}
            onStock={offerStock}
            onReview={() => review("Review my portfolio")}
            onBack={backToChat}
          />
        ) : view === "decisions" ? (
          <Register
            menu={menuBtn}
            decisions={decisions}
            state={decisionsState}
            onOpen={openDecision}
            onDelete={decision => void deleteDecision(decision)}
            onClearAll={() => void clearDecisions()}
            onBack={backToChat}
          />
        ) : (<>
        <header className="ctx">
          <div className="ctx-left">
            {menuBtn}
            <span>{ctxLabel}</span>
          </div>
          <div className="ctx-right">
            <span className="cap hide-sm">
              {reopened
                ? "Read-only"
                : committedSnapshot
                  ? `Portfolio as of ${fullDate(committedSnapshot.as_of)}${
                      result && !run ? ` · answered ${clock(result.portfolio.reviewed_at)}` : ""
                    }`
                  : ""}
            </span>
            {result &&
              !run &&
              !reopened &&
              !failure &&
              !showThread &&
              (saved ? (
                <span className="cap" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--sage)"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <path d="m5 12 5 5 9-10" />
                  </svg>
                  Saved {clock(saved.saved_at)}
                </span>
              ) : (
                <button
                  type="button"
                  className="btn primary small"
                  onClick={save}
                  disabled={saving}
                >
                  {saving ? "Saving…" : "Save decision"}
                </button>
              ))}
            {reopened && (
              <button type="button" className="btn ghost small" onClick={() => openView("decisions")}>
                <Bookmark size={15} aria-hidden="true" />
                All decisions
              </button>
            )}
            <button
              type="button"
              ref={detailsButton}
              className="btn secondary small details-btn"
              aria-expanded={panelOpen}
              aria-controls="details"
              onClick={() => {
                showDetails(undefined, null, true);
              }}
            >
              Details
            </button>
          </div>
          {run && (
            <div className="indet" aria-hidden="true">
              <span />
            </div>
          )}
        </header>
        <div className="scroll">
          <div className="col">
            {notice && (
              <p className="amber" role="alert" style={{ marginBottom: 16 }}>
                {notice}
              </p>
            )}
            {!run && !reopened && committedUnresolved.length > 0 && committedSnapshot && (
              <p className="amber" style={{ marginBottom: 16 }}>
                <button type="button" className="link amber" onClick={() => openView("portfolio")}>
                  Identify {committedUnresolved.length} holding
                  {committedUnresolved.length === 1 ? "" : "s"}
                </button>{" "}
                before asking. They aren&apos;t in the analysis until then.
              </p>
            )}
            {waitingHere.length > 0 && !activeProposal && (
              <section className="waiting" data-testid="pending-proposals-banner" aria-labelledby="waiting-title">
                <h2 id="waiting-title" className="waiting-title">Changes waiting for you</h2>
                <p className="waiting-sub">Prepared, not saved. Nothing changes until you confirm a review.</p>
                {waitingHere.map(p => {
                  const outdated = isStale(p, committedRevision);
                  return (
                    <div className="waiting-item" key={p.id} data-testid={`pending-proposal-item-${p.id}`}>
                      <div className="waiting-text">
                        <b>{ORIGIN[p.origin] || "Change"}</b>
                        <span className="cap">
                          {p.base_revision != null ? `Prepared from revision ${p.base_revision}` : "First portfolio"} · {p.differences.length} change{p.differences.length === 1 ? "" : "s"}
                          {p.warnings.length > 0 ? ` · ${p.warnings.length} warning${p.warnings.length === 1 ? "" : "s"}` : ""}
                        </span>
                      </div>
                      {outdated && <span className="chip amber">Out of date</span>}
                      <div className="waiting-actions">
                        <Button variant="ghost" size="sm" data-testid="cancel-pending-proposal" onClick={() => void cancelPendingProposal(p.id)}>Cancel proposal</Button>
                        <Button variant="secondary" size="sm" data-testid="reopen-proposal" onClick={() => openProposal(p)}>Review</Button>
                      </div>
                      {cancelFailure?.id === p.id && <p className="waiting-error" role="alert">{cancelFailure.message}</p>}
                    </div>
                  );
                })}
              </section>
            )}
            {center}
            {!showThread && !run && !reopened && !viewOpen && !clarify && !failure && draftBasis}
            <p className="sr-only" aria-live="polite">{result && !run ? "Answer ready." : ""}</p>
          </div>
        </div>
        {!reopened && (
          <Composer
            snapshot={committedSnapshot}
            unresolved={committedUnresolved.length}
            workflow={workflow}
            onWorkflow={setWorkflow}
            question={question}
            onQuestion={setQuestion}
            newCash={newCash}
            onNewCash={setNewCash}
            theme={theme}
            onTheme={updateTheme}
            themeCandidates={themeCandidates}
            onAddCandidate={addThemeCandidate}
            onRemoveCandidate={removeThemeCandidate}
            running={!!run || conversation.busy || capturingHere}
            inThread={showThread}
            home={home}
            collapsed={collapsed && !!(answer || failure || run)}
            onExpand={() => setCollapsed(false)}
            onSubmit={() => {
              if (resuming && showThread && workflow) {
                // Opened from a clarification: the explicit form inputs go to the conversation as a new run.
                // The theme is agreed in a second step, once the analyst has the complete form.
                if (workflow === "new-cash")
                  submitWorkflow(question, { workflow: "run_new_cash_allocation", new_cash: newCash });
                else
                  submitWorkflow(question.trim() || `Explore theme: ${theme.name}`, {
                    workflow: "run_theme_exploration",
                    theme: { ...theme, confirmed: false },
                  });
                setQuestion("");
                setNewCash(current => ({ ...current, confirmed: false }));
              } else if (workflow === "new-cash") void analyze({ question, newCash, stock: null });
              else if (workflow === "theme")
                void analyze({
                  question: question.trim() || `Explore theme: ${theme.name}`,
                  newCash: null,
                  theme: { ...theme, confirmed: false },
                  themeCandidates,
                });
              else {
                // A workflow form keeps the typed question as its own; everything else has been sent.
                const next = route(question, committedSnapshot);
                const opensForm =
                  !isConversation(question, committedSnapshot) && (next.kind === "new-cash" || next.kind === "theme");
                ask(question);
                if (!opensForm) setQuestion("");
              }
            }}
          />
        )}
        {homeBelow}
        </>)}
      </main>
      <Panel
        tab={tab}
        onTab={openTab}
        onClose={closeDetails}
        title={panelTitle}
        subtitle={panelSubtitle}
        inert={drawer}
        focus={focus}
        snapshot={draftSnapshot}
        setSnapshot={setDraftSnapshot}
        averageCosts={draftAverageCosts}
        unresolved={draftUnresolved}
        onImported={adopt}
        baseRevision={committedRevision}
        onProposal={handleProposal}
        settings={draftSettings}
        setSettings={setDraftSettings}
        benchmark={draftBenchmark}
        setBenchmark={setDraftBenchmark}
        result={reopened ? null : result}
        decision={reopened}
        running={!!run}
        onError={setNotice}
        prices={prices}
        refreshing={refreshing}
        onRefreshPrices={() => void refreshPrices(true)}
        isDirty={isDirty}
        reviewing={reviewing}
        onReviewChanges={reviewChanges}
        onDiscardDraft={discardDraft}
        onAnalyzeDraft={() => void analyzeHypotheticalDraft()}
        onPreviewProposal={openProposal}
        pendingProposals={pendingProposals}
        onReopenProposal={openProposal}
      />
      <PortfolioPreview
        proposal={activeProposal}
        snapshot={committedSnapshot}
        committedRevision={committedRevision}
        onConfirm={() => void confirmActiveProposal()}
        onCancel={() => void cancelActiveProposal()}
        onClose={closeProposal}
        confirming={confirming}
        cancelling={cancelling}
        error={previewError}
        refused={proposalRefused}
        fallbackFocus={() => document.getElementById(view === "chat" ? "ask" : `${view}-title`)}
      />
      <p className="sr-only" aria-live="polite">
        {receipt ? `Saved as portfolio revision ${receipt.new_revision}. No trade was placed.` : ""}
      </p>
      {receipt && (
        <div className="receipt-toast">
          <Toast
            title={`Saved as portfolio revision ${receipt.new_revision}`}
            description={`${receipt.previous_revision != null ? `Was revision ${receipt.previous_revision}` : "Your first revision"} · saved ${clock(receipt.committed_at)} · Your records only. No trade was placed.`}
            duration={10000}
            onOpenChange={open => { if (!open) setReceipt(null); }}
          />
        </div>
      )}
      <div
        className="scrim"
        onClick={() => {
          if (railOpen) closeRail();
          else closeDetails();
        }}
        aria-hidden="true"
      />
    </div>
  );
}
