"""Record schemas used by the Evidence & QA agent to re-validate agent output before persistence."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import ClauseType, DeadlineKind, ObligationCategory


class ObligationRecord(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5)
    category: ObligationCategory
    confidence: float = Field(ge=0.0, le=1.0)
    fingerprint: str = Field(min_length=8)


class ClauseRecord(BaseModel):
    clause_type: ClauseType
    summary: str = Field(min_length=3)
    fingerprint: str = Field(min_length=8)


class DeadlineRecord(BaseModel):
    label: str = Field(min_length=3)
    kind: DeadlineKind
    due_date: date | None
    calculation_trace: list[str] = Field(min_length=1)
    fingerprint: str = Field(min_length=8)
