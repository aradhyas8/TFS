"use client";

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
import Rail from "../../components/desk/Rail";
import Composer from "../../components/desk/Composer";
import Register from "../../components/desk/Register";
import Panel, { evidenceFor, ImportCsv } from "../../components/desk/Panel";
import PortfolioPreview from "../../components/desk/PortfolioPreview";
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
import { Button } from "@/components/arc/button/button";
import { useConversation } from "../../components/desk/useConversation";
import { clock, fullDate, newCashLabel, pct, shortDate } from "../../components/desk/format";

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
  const [isProposalStale, setIsProposalStale] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

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
    const stale =
      proposal.status === "stale" ||
      (committedRevision != null && proposal.base_revision != null && proposal.base_revision !== committedRevision);
    setIsProposalStale(stale);
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
  const [answer, setAnswer] = useState<(Sent & { analysis: Analysis }) | null>(null);
  const [failure, setFailure] = useState<(Sent & { message: string }) | null>(null);
  const [clarify, setClarify] = useState<Extract<Route, { kind: "clarify" }> | null>(null);
  const [saved, setSaved] = useState<SavedDecision | null>(null);
  const [saving, setSaving] = useState(false);
  const [decisions, setDecisions] = useState<SavedDecision[]>([]);
  const [reopened, setReopened] = useState<SavedDecision | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("holdings");
  const [focus, setFocus] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [prices, setPrices] = useState<PriceRefresh | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const runId = useRef(0);
  // Durable conversation: threads, messages and runs live in the backend, never in the cached last answer.
  const conversation = useConversation();
  // The stored result the Details panel reads while a conversation is on screen: the one the investor last opened.
  const [threadResult, setThreadResult] = useState<{ id: string; analysis: Analysis } | null>(null);
  // Which decision each stored result was saved as in this session: artifact ID to decision ID. The decision
  // itself is always read from the decisions list, so a deleted decision is no longer shown as saved.
  // ponytail: kept in memory, so after a reload a result can be saved a second time. Record the artifact ID
  // on the decision if duplicate register entries become a problem.
  const [savedResults, setSavedResults] = useState<Record<string, string>>({});
  const [savingResult, setSavingResult] = useState<string | null>(null);
  const [saveFailed, setSaveFailed] = useState<string | null>(null);
  // A workflow form opened from a clarification: its submission is a new run in this conversation.
  const [resuming, setResuming] = useState(false);
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
      .then(setDecisions)
      .catch(() => setNotice("Saved decisions couldn't be loaded."));
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

  const isDirty = isDraftDirty(committedPortfolio, {
    snapshot: draftSnapshot,
    settings: draftSettings,
    benchmark: draftBenchmark,
  });

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
      setIsProposalStale(false);
      refreshProposals();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to prepare portfolio review.";
      if (msg.includes("409") || msg.toLowerCase().includes("stale")) {
        setIsProposalStale(true);
        setNotice(
          "The saved portfolio was updated. Your draft edits are preserved — please review again against current state."
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
    setIsProposalStale(false);
    setPreviewError(null);
    setPendingProposals(prev => [proposal, ...prev.filter(p => p.id !== proposal.id)]);
  }

  async function cancelPendingProposal(proposalId: string) {
    try {
      await cancelProposal(proposalId);
    } catch {
      // Ignored if network issue or already cancelled
    } finally {
      setPendingProposals(prev => prev.filter(p => p.id !== proposalId));
      if (activeProposal?.id === proposalId) {
        setActiveProposal(null);
        setIsProposalStale(false);
        setPreviewError(null);
      }
    }
  }

  async function confirmActiveProposal() {
    if (!activeProposal) return;
    setConfirming(true);
    setPreviewError(null);
    try {
      const res = await confirmProposal(activeProposal.id);
      const confirmedId = activeProposal.id;
      adoptCommitted(res, activeProposal);
      setActiveProposal(null);
      setIsProposalStale(false);
      setPendingProposals(prev => prev.filter(p => p.id !== confirmedId));
      refreshProposals();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Confirmation failed.";
      if (
        msg.includes("409") ||
        msg.toLowerCase().includes("stale") ||
        msg.toLowerCase().includes("conflict")
      ) {
        setIsProposalStale(true);
        setPreviewError(
          "This proposal is stale because the committed portfolio changed. Your draft edits are preserved."
        );
      } else {
        setPreviewError(msg);
      }
    } finally {
      setConfirming(false);
    }
  }

  async function cancelActiveProposal() {
    if (!activeProposal) return;
    setCancelling(true);
    const cancellingId = activeProposal.id;
    try {
      await cancelProposal(activeProposal.id);
    } catch {
      // Ignored if network issue or already cancelled
    } finally {
      setActiveProposal(null);
      setCancelling(false);
      setIsProposalStale(false);
      setPreviewError(null);
      setPendingProposals(prev => prev.filter(p => p.id !== cancellingId));
      refreshProposals();
    }
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
    !registerOpen && !reopened && !run && !clarify && !failure && (!!conversation.openId || !!conversation.pending);
  const result = (showThread ? threadResult?.analysis : answer?.analysis) ?? null;
  const evidence = evidenceFor(reopened ? null : result, reopened);
  const cite = {
    numbers: evidence.numbers,
    onCite: (id: string) => {
      setTab("evidence");
      setFocus(id);
      setPanelOpen(true);
    },
  };
  const openTab = (next: Tab) => {
    setTab(next);
    setFocus(null);
    setPanelOpen(true);
  };

  /** Ordinary analysis uses COMMITTED portfolio state */
  async function analyze(sent: Sent) {
    if (!committedSnapshot) return;
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
    setRegisterOpen(false);
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
      setAnswer({ ...sent, analysis });
      setTab(sent.rebalance ? "proposed" : "evidence");
      setFocus(null);
      try {
        localStorage.setItem(ANSWER_KEY, JSON.stringify({ ...sent, analysis }));
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
    setRegisterOpen(false);
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

      const artifactRef = await createArtifact({
        portfolio: candidatePortfolio,
        label: "Hypothetical Draft Portfolio",
        source_artifact_id: committedRevision ? `art-rev-${committedRevision}` : null,
      });

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
    setRegisterOpen(false);
    setWorkflow(null);
    setCollapsed(false);
    setNotice("");
    setRailOpen(false);
  }

  function openThread(id: string) {
    showConversation();
    conversation.open(id);
  }

  function chat(text: string) {
    // A question asked from another view (the register, a workflow answer) starts a new conversation
    // rather than landing in a thread that is not on screen.
    if (!showThread) conversation.close();
    showConversation();
    void conversation.send(text.trim());
  }

  function ask(text: string) {
    if (isConversation(text, committedSnapshot)) {
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
    setRegisterOpen(false);
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
      const record = await post<SavedDecision>("/api/decisions", { result: analysis, ...decisionFor(analysis, null, source) });
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
    setRegisterOpen(false);
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
    setRegisterOpen(false);

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
  const ctxLabel = registerOpen
    ? "Decisions Register"
    : reopened
      ? `Decision · ${shortDate(reopened.saved_at)}`
      : showThread
        ? "Conversation"
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
    const open = () => setThreadResult({ id: reference.id, analysis });
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
    void conversation.send(text.trim(), workflowInput);
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
  let center;
  if (registerOpen)
    center = (
      <Register
        decisions={decisions}
        onOpen={decision => {
          setRegisterOpen(false);
          setReopened(decision);
          setClarify(null);
          setTab("evidence");
          setFocus(null);
          setRailOpen(false);
        }}
        onClose={() => setRegisterOpen(false)}
      />
    );
  else if (reopened)
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
        {isDirty && (
          <p className="cap" data-testid="draft-note" style={{ marginTop: 24 }}>
            Answers use your saved portfolio. Unsaved draft edits are not included until you review and confirm them.
          </p>
        )}
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
        <p className="body" style={{ maxWidth: 520 }}>
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
              : "What should we look at?"}
        </h1>
        {workflow === "theme" ? (
          <p className="body" style={{ maxWidth: 560 }}>
            Frame an investment theme, name candidate stocks or let the analyst propose them, and test
            whether exposure makes sense against your portfolio.
          </p>
        ) : workflow === "new-cash" ? (
          <p className="body" style={{ maxWidth: 560 }}>
            The analyst screens what you could add to, researches candidates that could change the
            answer, and compares them with keeping cash and doing nothing. You place any order yourself.
          </p>
        ) : (
          <div className="starters">
            <button
              type="button"
              className="starter"
              disabled={committedUnresolved.length > 0}
              onClick={() => {
                setQuestion("Review my portfolio");
                review("Review my portfolio");
              }}
            >
              <span>Review my portfolio</span>
              <span className="m">Exposure, concentration, fund overlap and your rules</span>
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
              <span>/new-cash</span>
              <span className="m">Decide where new money should go</span>
            </button>
            <button
              type="button"
              className="starter"
              disabled={committedUnresolved.length > 0}
              onClick={() =>
                void analyze({
                  question: "Should I rebalance my portfolio?",
                  newCash: null,
                  rebalance: true,
                })
              }
            >
              <span>/rebalance</span>
              <span className="m">Should anything change? Reduce, keep or add, within your rules</span>
            </button>
            <button
              type="button"
              className="starter"
              disabled={committedUnresolved.length > 0}
              onClick={() => {
                setWorkflow("theme");
                setCollapsed(false);
                document.getElementById("ask")?.focus();
              }}
            >
              <span>/theme</span>
              <span className="m">Test an idea against a short list of candidate stocks</span>
            </button>
            <button
              type="button"
              className="starter"
              disabled={committedUnresolved.length > 0}
              onClick={() => {
                setWorkflow(null);
                setQuestion("/stock ");
                document.getElementById("ask")?.focus();
              }}
            >
              <span>/stock</span>
              <span className="m">
                Analyze one company you own. Or just ask: &ldquo;What do you think about{" "}
                {committedSnapshot.positions.find(row => row.kind === "stock")?.ticker || "AVGO"}
                ?&rdquo;
              </span>
            </button>
          </div>
        )}
      </div>
    );

  return (
    <div className={`desk${panelOpen ? " panel-open" : ""}${railOpen ? " rail-open" : ""}`}>
      <a className="skip" href="#details">
        Skip to details
      </a>
      <Rail
        snapshot={committedSnapshot}
        unresolved={committedUnresolved}
        settings={committedSettings}
        result={result}
        decisions={decisions}
        currentId={reopened?.id ?? saved?.id ?? null}
        threads={conversation.threads}
        threadsError={conversation.threadsError}
        threadId={showThread ? conversation.openId : null}
        onThread={openThread}
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
        onHoldings={() => {
          openTab("holdings");
          setRailOpen(false);
        }}
        onOpen={decision => {
          setRegisterOpen(false);
          setReopened(decision);
          setClarify(null);
          setTab("evidence");
          setFocus(null);
          setRailOpen(false);
        }}
        onAllDecisions={() => {
          setReopened(null);
          setRegisterOpen(true);
          setClarify(null);
          setFocus(null);
          setRailOpen(false);
        }}
        onStock={offerStock}
        onDelete={decision => void deleteDecision(decision)}
        onClearAll={() => void clearDecisions()}
      />
      <main className="center">
        <header className="ctx">
          <div className="ctx-left">
            <button
              type="button"
              className="icon-btn menu-btn"
              aria-label="Open portfolio and decisions"
              onClick={() => setRailOpen(true)}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
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
            <button
              type="button"
              className="btn secondary small details-btn"
              onClick={() => setPanelOpen(true)}
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
            {pendingProposals.length > 0 && !activeProposal && (
              <aside
                className="pending-proposal-banner"
                data-testid="pending-proposal-banner"
                role="status"
                aria-label="Pending portfolio proposal"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "12px 16px",
                  marginBottom: 16,
                  background: "rgba(224, 163, 92, 0.12)",
                  border: "1px solid var(--amber)",
                  borderRadius: 6,
                  gap: 12,
                }}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: "var(--amber)" }}>
                    Pending review: {
                      pendingProposals[0].origin === "import"
                        ? "Holdings import"
                        : pendingProposals[0].origin === "manual"
                          ? "Manual edit"
                          : pendingProposals[0].origin === "identify"
                            ? "Identity correction"
                            : "Action proposal"
                    }
                  </span>
                  <span className="cap" style={{ color: "var(--text-2)" }}>
                    {pendingProposals[0].base_revision != null
                      ? `Prepared from revision ${pendingProposals[0].base_revision}`
                      : "Initial portfolio creation"}
                  </span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button
                    type="button"
                    className="btn secondary small"
                    data-testid="cancel-pending-proposal"
                    onClick={() => void cancelPendingProposal(pendingProposals[0].id)}
                  >
                    Discard
                  </button>
                  <button
                    type="button"
                    className="btn primary small"
                    data-testid="reopen-pending-proposal"
                    onClick={() => openProposal(pendingProposals[0])}
                  >
                    Reopen preview
                  </button>
                </div>
              </aside>
            )}
            {notice && (
              <p className="amber" role="alert" style={{ marginBottom: 16 }}>
                {notice}
              </p>
            )}
            {!run && !reopened && committedUnresolved.length > 0 && committedSnapshot && (
              <p className="amber" style={{ marginBottom: 16 }}>
                <button type="button" className="link amber" onClick={() => openTab("holdings")}>
                  Identify {committedUnresolved.length} holding
                  {committedUnresolved.length === 1 ? "" : "s"}
                </button>{" "}
                before asking. They aren&apos;t in the analysis until then.
              </p>
            )}
            {pendingProposals.length > 0 && !activeProposal && (
              <div
                className="pgroup pending-proposals-banner"
                data-testid="pending-proposals-banner"
                role="region"
                aria-label="Pending portfolio proposals"
                style={{
                  background: "var(--raised)",
                  border: "1px solid var(--rule-strong)",
                  padding: "12px 14px",
                  borderRadius: 8,
                  marginBottom: 16,
                }}
              >
                <div className="pv" style={{ alignItems: "center" }}>
                  <div>
                    <span className="lbl amber">
                      Pending proposal{pendingProposals.length === 1 ? "" : "s"} ({pendingProposals.length})
                    </span>
                    <p className="cap" style={{ marginTop: 2 }}>
                      Prepared actions waiting for confirmation.
                    </p>
                  </div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                  {pendingProposals.map((p, idx) => {
                    const isStale =
                      p.status === "stale" ||
                      (committedRevision != null && p.base_revision != null && p.base_revision !== committedRevision);
                    return (
                      <div
                        key={p.id}
                        className="pv"
                        style={{
                          alignItems: "center",
                          padding: "6px 0",
                          borderTop: idx > 0 ? "1px solid var(--rule)" : undefined,
                        }}
                        data-testid={`pending-proposal-item-${p.id}`}
                      >
                        <div>
                          <span style={{ fontWeight: 500, fontSize: 13 }}>
                            {p.origin === "manual"
                              ? "Manual edit"
                              : p.origin === "import"
                                ? "Holdings import"
                                : p.origin === "identify"
                                  ? "Identity correction"
                                  : "Action proposal"}
                          </span>
                          <span className="cap" style={{ marginLeft: 8 }}>
                            {p.base_revision != null ? `Base rev ${p.base_revision}` : "Initial creation"} ·{" "}
                            {p.differences.length} change{p.differences.length === 1 ? "" : "s"}
                            {p.warnings.length > 0 ? ` · ${p.warnings.length} warning(s)` : ""}
                          </span>
                          {isStale && (
                            <span className="cap amber" style={{ marginLeft: 8, fontWeight: 500 }}>
                              (stale)
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          className="btn secondary small"
                          data-testid="reopen-proposal"
                          onClick={() => openProposal(p)}
                        >
                          Reopen review
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {center}
            <p className="sr-only" aria-live="polite">
              {result && !run ? "Answer ready." : ""}
            </p>
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
            running={!!run || conversation.busy}
            inThread={showThread}
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
      </main>
      <Panel
        tab={tab}
        onTab={openTab}
        onClose={() => setPanelOpen(false)}
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
      {activeProposal && (
        <PortfolioPreview
          proposal={activeProposal}
          onConfirm={confirmActiveProposal}
          onCancel={cancelActiveProposal}
          confirming={confirming}
          cancelling={cancelling}
          error={previewError}
          isStale={isProposalStale}
        />
      )}
      <div
        className="scrim"
        onClick={() => {
          setPanelOpen(false);
          setRailOpen(false);
        }}
        aria-hidden="true"
      />
    </div>
  );
}
