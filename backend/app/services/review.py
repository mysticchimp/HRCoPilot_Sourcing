"""Review queue over scored role_candidates (shortlist / bench / reviewing)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Candidate, RoleCandidate
from app.services.scoring import _scored_card

ReviewStatus = Literal["reviewing", "shortlisted", "benched"]
VALID_STATUSES: tuple[str, ...] = ("reviewing", "shortlisted", "benched")


def _review_counts(db: Session, role_id: uuid.UUID) -> dict[str, int]:
    """Counts of scored candidates per review_status for a role."""
    rows = db.execute(
        select(RoleCandidate.review_status, func.count())
        .join(Candidate, Candidate.id == RoleCandidate.candidate_id)
        .where(
            RoleCandidate.role_id == role_id,
            RoleCandidate.scored_at.is_not(None),
            RoleCandidate.total_score.is_not(None),
            Candidate.is_complete_profile.is_(True),
        )
        .group_by(RoleCandidate.review_status)
    ).all()
    counts = {s: 0 for s in VALID_STATUSES}
    for status, n in rows:
        if status in counts:
            counts[status] = int(n)
    return counts


def list_review_queue(
    db: Session, role_id: uuid.UUID, status: str
) -> dict[str, Any]:
    """Scored candidates for a role filtered by review_status, score DESC."""
    if status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of: {', '.join(VALID_STATUSES)}"
        )

    rows = db.execute(
        select(Candidate, RoleCandidate)
        .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
        .where(
            RoleCandidate.role_id == role_id,
            RoleCandidate.review_status == status,
            RoleCandidate.scored_at.is_not(None),
            RoleCandidate.total_score.is_not(None),
            Candidate.is_complete_profile.is_(True),
        )
        .order_by(RoleCandidate.total_score.desc().nullslast())
    ).all()

    candidates = []
    for cand, rc in rows:
        card = _scored_card(cand, rc)
        card["review_status"] = rc.review_status
        candidates.append(card)

    return {
        "counts": _review_counts(db, role_id),
        "candidates": candidates,
        "status": status,
        "count": len(candidates),
    }


def set_review_status(
    db: Session,
    role_id: uuid.UUID,
    candidate_id: uuid.UUID,
    status: str,
) -> dict[str, Any]:
    """Update review_status for one (role, candidate) pair; return fresh counts."""
    if status not in VALID_STATUSES:
        raise ValueError(
            f"status must be one of: {', '.join(VALID_STATUSES)}"
        )

    rc = db.execute(
        select(RoleCandidate).where(
            RoleCandidate.role_id == role_id,
            RoleCandidate.candidate_id == candidate_id,
        )
    ).scalar_one_or_none()
    if not rc:
        raise LookupError("candidate not found for this role")
    if rc.scored_at is None:
        raise ValueError("candidate has not been scored")

    rc.review_status = status
    db.commit()

    return {
        "ok": True,
        "candidate_id": str(candidate_id),
        "review_status": status,
        "counts": _review_counts(db, role_id),
    }
