"""Deterministic reconciliation of source-adapter facts, independent of an LLM.

These records are an internal boundary, not model output. Source adapters must
establish field ownership, authority and session identity before constructing
them. The agent may select sources but cannot assign their authority or identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated, Literal, Self, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Availability = Literal["available", "full", "waitlist", "cancelled", "unknown"]
Money = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, decimal_places=2)]
Count = Annotated[int, Field(ge=0, strict=True)]
Identifier = Annotated[str, Field(min_length=1, max_length=240)]
Reason = Literal["date_conflict", "undated", "price_conflict", "status_conflict", "count_conflict"]


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SessionEvidence(EvidenceModel):
    """One parsed, source-owned occurrence; IDs come from the retrieval registry."""

    source_id: Identifier
    session_key: Identifier
    kind: Literal["seasonal", "registration"]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    price_usd: Money | None = None
    availability: Availability = "unknown"
    waitlist_count: Count | None = None

    @model_validator(mode="after")
    def validate_occurrence(self) -> Self:
        if (self.starts_at is None) != (self.ends_at is None):
            raise ValueError("An occurrence needs both start and end")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("Occurrence end must follow start")
        if self.kind == "seasonal" and (
            self.availability != "unknown" or self.waitlist_count is not None
        ):
            raise ValueError("Seasonal schedules do not establish registration availability")
        return self


class VerifiedSession(EvidenceModel):
    """Reconciled discovery candidate, never a confirmed personal reservation."""

    session_key: Identifier
    title: str
    starts_at: AwareDatetime | None
    ends_at: AwareDatetime | None
    price_usd: Money | None
    availability: Availability
    waitlist_count: Count | None
    # Public discovery has no personal registration context, even when count is known.
    user_waitlist_position: None = None
    evidence_ids: tuple[str, ...]
    needs_confirmation: bool
    reasons: tuple[Reason, ...]


T = TypeVar("T")


def _consensus(values: Sequence[T]) -> tuple[T, bool]:
    first = values[0]
    return first, any(value != first for value in values[1:])


def reconcile_session(records: Sequence[SessionEvidence]) -> VerifiedSession:
    """Prefer dated registration records, retain all evidence, fail closed on conflict.

    Missing fields on a registration record are not filled from an older seasonal
    offer. Equal-authority disagreement cannot be resolved by input ordering.
    """
    if not records:
        raise ValueError("At least one source record is required")
    if len({record.session_key for record in records}) != 1:
        raise ValueError("Cannot combine different session identities")
    unique: dict[str, SessionEvidence] = {}
    for record in records:
        if record.source_id in unique and unique[record.source_id] != record:
            raise ValueError("A source ID cannot refer to inconsistent records")
        unique[record.source_id] = record
    ordered = sorted(unique.values(), key=lambda record: record.source_id)
    registrations = [record for record in ordered if record.kind == "registration"]
    authoritative = registrations or ordered
    reasons: list[Reason] = []

    (start, end), dates_conflict = _consensus(
        [(record.starts_at, record.ends_at) for record in authoritative]
    )
    if dates_conflict:
        start, end = None, None
        reasons.append("date_conflict")
    elif start is None:
        reasons.append("undated")

    price, prices_conflict = _consensus([record.price_usd for record in authoritative])
    if prices_conflict:
        price = None
        reasons.append("price_conflict")

    status, statuses_conflict = _consensus([record.availability for record in authoritative])
    if statuses_conflict:
        reasons.append("status_conflict")
    if statuses_conflict or start is None or not registrations:
        status = "unknown"

    count, counts_conflict = _consensus([record.waitlist_count for record in authoritative])
    if counts_conflict:
        reasons.append("count_conflict")
    if counts_conflict or status not in {"full", "waitlist"}:
        count = None

    return VerifiedSession(
        session_key=ordered[0].session_key,
        title=authoritative[0].title,
        starts_at=start,
        ends_at=end,
        price_usd=price,
        availability=status,
        waitlist_count=count,
        evidence_ids=tuple(record.source_id for record in ordered),
        needs_confirmation=bool(reasons) or status == "unknown",
        reasons=tuple(reasons),
    )
