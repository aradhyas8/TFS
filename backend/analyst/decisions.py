from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import AwareDatetime

from .schemas import (
    AnalysisResult,
    CompanyResearch,
    DecisionAction,
    DecisionAlternative,
    DecisionConclusion,
    DecisionReasoning,
    EvidenceReference,
    ResearchDocument,
    ResearchFact,
    SavedDecision,
    UserConfirmedAction,
)


def _doc_to_reference(doc: ResearchDocument) -> EvidenceReference:
    return EvidenceReference(
        id=doc.id,
        title=doc.title,
        source=doc.authority,
        as_of=doc.as_of or doc.published_on,
        url=doc.url,
        excerpt=doc.excerpt,
    )


def extract_evidence_references(result: AnalysisResult) -> list[EvidenceReference]:
    references: list[EvidenceReference] = []
    seen_ids: set[str] = set()

    # 1. Gather all documents and facts from any research present in the result
    documents: dict[str, ResearchDocument] = {}
    facts: dict[str, ResearchFact] = {}

    def collect_research(res: CompanyResearch | None) -> None:
        if not res:
            return
        for doc in res.documents:
            documents[doc.id] = doc
        for fact in res.facts:
            facts[fact.id] = fact

    if result.stock:
        collect_research(result.stock.research)
    if result.allocation:
        for st in result.allocation.stocks:
            collect_research(st.research)
    if result.reunderwriting:
        for st in result.reunderwriting.stocks:
            collect_research(st.research)
        for res in result.reunderwriting.research.values():
            collect_research(res)
    if result.theme:
        for st in result.theme.stocks:
            collect_research(st.research)

    # 2. Add cited evidence from recommendation evidence_ids first
    cited_ids = getattr(result.recommendation, "evidence_ids", []) or []
    for ev_id in cited_ids:
        if ev_id in seen_ids:
            continue
        seen_ids.add(ev_id)
        if ev_id in documents:
            references.append(_doc_to_reference(documents[ev_id]))
        elif ev_id in facts:
            fact = facts[ev_id]
            doc = next((documents[d_id] for d_id in fact.document_ids if d_id in documents), None)
            references.append(
                EvidenceReference(
                    id=fact.id,
                    title=f"{fact.metric}: {fact.value or 'unknown'} {fact.unit}".strip(),
                    source=doc.authority if doc else fact.definition,
                    as_of=fact.period_end,
                    url=doc.url if doc else None,
                    excerpt=fact.definition,
                )
            )
        else:
            references.append(
                EvidenceReference(
                    id=ev_id,
                    title=ev_id,
                    source="Analysis citation",
                    as_of=None,
                )
            )

    # 3. Add any other primary research documents
    for doc in documents.values():
        if doc.id not in seen_ids:
            seen_ids.add(doc.id)
            references.append(_doc_to_reference(doc))

    # 4. Add ETF fund facts from comparison if present
    if result.comparison:
        for ff in result.comparison.inputs.fund_facts:
            fid = f"fund-fact-{ff.position_id}"
            if fid not in seen_ids:
                seen_ids.add(fid)
                references.append(
                    EvidenceReference(
                        id=fid,
                        title=f"{ff.position_id} sponsor fund facts ({ff.exposure})",
                        source=ff.source,
                        as_of=ff.as_of,
                        url=ff.source_url,
                    )
                )

    # 5. Add indicative quotes and FX references from the portfolio
    for pos in result.portfolio.positions:
        if pos.quote_used:
            qid = f"quote-{pos.supplied.id}"
            if qid not in seen_ids:
                seen_ids.add(qid)
                ticker_label = pos.supplied.ticker or pos.supplied.id
                references.append(
                    EvidenceReference(
                        id=qid,
                        title=f"{ticker_label} mark ({pos.quote_used.value} {pos.quote_used.currency}, {pos.quote_used.status})",
                        source=pos.quote_used.source,
                        as_of=pos.quote_used.as_of,
                    )
                )
        if pos.fx_used:
            fx_id = f"fx-{pos.fx_used.from_currency}-{pos.fx_used.to_currency}"
            if fx_id not in seen_ids:
                seen_ids.add(fx_id)
                references.append(
                    EvidenceReference(
                        id=fx_id,
                        title=f"FX {pos.fx_used.from_currency}/{pos.fx_used.to_currency} indicative rate ({pos.fx_used.rate})",
                        source=pos.fx_used.source,
                        as_of=pos.fx_used.as_of,
                    )
                )

    return references


def extract_decision(
    result: AnalysisResult,
    decision_id: str | None = None,
    confirmed_action: UserConfirmedAction | None = None,
) -> SavedDecision:
    now = datetime.now(timezone.utc)
    ev_refs = extract_evidence_references(result)
    rec = result.recommendation

    return SavedDecision(
        id=decision_id or f"dec-{uuid.uuid4().hex[:12]}",
        saved_at=now,
        as_of=result.portfolio.as_of,
        question=result.question,
        evidence_references=ev_refs,
        reasoning=DecisionReasoning(
            reason=rec.reason,
            assumptions=list(rec.assumptions),
            uncertainty=list(rec.uncertainty),
            what_could_change=list(rec.what_could_change),
            downside=rec.downside,
            alternatives=[
                DecisionAlternative(action=alt.action, reason=alt.reason)
                for alt in rec.alternatives
            ],
        ),
        conclusion=DecisionConclusion(
            preferred_action=rec.preferred_action,
            amount=rec.amount,
        ),
        confirmed_action=confirmed_action,
    )


class DecisionStore:
    def __init__(self, directory: Path | str | None = None) -> None:
        if directory is not None:
            self.directory = Path(directory)
        elif "DECISIONS_DIR" in os.environ:
            self.directory = Path(os.environ["DECISIONS_DIR"])
        else:
            self.directory = Path(__file__).resolve().parents[1] / "data" / "decisions"
        self.directory.mkdir(parents=True, exist_ok=True)

    def _file_path(self, decision_id: str) -> Path:
        safe_id = Path(decision_id).name
        return self.directory / f"{safe_id}.json"

    def save(self, decision: SavedDecision) -> SavedDecision:
        self.directory.mkdir(parents=True, exist_ok=True)
        file_path = self._file_path(decision.id)
        # Write JSON to plain local file
        data = decision.model_dump(mode="json")
        temp_path = file_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(file_path)
        return decision

    def get(self, decision_id: str) -> SavedDecision | None:
        file_path = self._file_path(decision_id)
        if not file_path.exists() or not file_path.is_file():
            return None
        raw = file_path.read_text(encoding="utf-8")
        return SavedDecision.model_validate_json(raw)

    def list_decisions(self) -> list[SavedDecision]:
        if not self.directory.exists():
            return []
        decisions: list[SavedDecision] = []
        for file in sorted(self.directory.glob("*.json")):
            try:
                decisions.append(SavedDecision.model_validate_json(file.read_text(encoding="utf-8")))
            except Exception:
                continue
        # Sort newest saved first
        decisions.sort(key=lambda d: d.saved_at, reverse=True)
        return decisions

    def delete(self, decision_id: str) -> bool:
        file_path = self._file_path(decision_id)
        if not file_path.is_file():
            return False
        file_path.unlink()
        return True

    def clear(self) -> int:
        files = list(self.directory.glob("*.json")) if self.directory.exists() else []
        for file in files:
            file.unlink()
        return len(files)

    def confirm_action(
        self,
        decision_id: str,
        action: DecisionAction,
        notes: str | None = None,
        confirmed_at: AwareDatetime | None = None,
    ) -> SavedDecision:
        decision = self.get(decision_id)
        if decision is None:
            raise KeyError(f"Decision {decision_id} not found.")
        now = confirmed_at or datetime.now(timezone.utc)
        updated = decision.model_copy(
            update={
                "confirmed_action": UserConfirmedAction(
                    action=action,
                    confirmed_at=now,
                    notes=notes,
                )
            }
        )
        self.save(updated)
        return updated
