"""Backend-owned primary evidence. No model-supplied facts or arbitrary URLs."""
import os
from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter

from .schemas import CompanyResearch, Position


class ResearchProvider(Protocol):
    async def company(self, position: Position, as_of: date) -> CompanyResearch: ...


class ReviewedResearchProvider:
    """Reads independently reviewed filing and issuer extracts with original links.

    This adapter does not claim live retrieval or automatic XBRL verification.
    A missing record returns unknown evidence, never manufactured company facts.
    """

    def __init__(self, records: dict[str, CompanyResearch] | None = None) -> None:
        self.records = records or {}

    @classmethod
    def from_environment(cls) -> "ReviewedResearchProvider":
        path = os.environ.get("RESEARCH_REFERENCE_FILE")
        records = TypeAdapter(dict[str, CompanyResearch]).validate_json(Path(path).read_text(encoding="utf-8")) if path else {}
        return cls(records)

    async def company(self, position: Position, as_of: date) -> CompanyResearch:
        record = self.records.get(position.company_id or "")
        if record is None:
            return CompanyResearch(company_id=position.company_id or position.id, sector="unknown",
                                   cyclical=None, documents=[], facts=[],
                                   issues=["Primary filing and issuer evidence is unavailable; company facts remain unknown."])
        if record.company_id != position.company_id:
            raise ValueError("Research issuer conflicts with the selected identity.")
        result = record.model_copy(deep=True)
        for doc in result.documents:
            if doc.published_on > as_of or doc.as_of > as_of or not doc.excerpt.strip():
                doc.available = False
                result.issues.append(f"{doc.id}: unavailable or later than the decision date.")
        return result
