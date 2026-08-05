"""Call the external Contra6 Scoring API and persist results on role_candidates."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Candidate, Role, RoleCandidate
from app.services.pull_batch import (
    INCOMPLETE_REASON,
    _candidate_row,
    list_incomplete_for_role,
)

logger = logging.getLogger("sourcing.scoring")

SCORE_TIMEOUT_SECONDS = 180
SCORE_SLOW_MESSAGE = (
    "Scoring is taking longer than expected — the scoring service may be "
    "cold-starting. Please try again in a minute."
)
SCORE_UNREACHABLE_MESSAGE = (
    "Could not reach the scoring service — please try again shortly."
)


class ScoringTransientError(Exception):
    """Timeout / connection failures talking to the scoring API."""

    def __init__(self, message: str = SCORE_SLOW_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


def _scored_card(cand: Candidate, rc: RoleCandidate) -> dict[str, Any]:
    row = _candidate_row(cand)
    row["candidate_id"] = str(cand.id)
    row["total_score"] = float(rc.total_score) if rc.total_score is not None else None
    row["component_breakdown"] = rc.component_breakdown
    row["matched_signals"] = list(rc.matched_signals or [])
    row["reasoning"] = rc.reasoning
    row["scored_at"] = rc.scored_at.isoformat() if rc.scored_at else None
    row["score_status"] = "scored"
    row["review_status"] = getattr(rc, "review_status", None) or "reviewing"
    return row


def _incomplete_card(cand: Candidate, rc: RoleCandidate | None = None) -> dict[str, Any]:
    row = _candidate_row(cand)
    row["candidate_id"] = str(cand.id)
    row["total_score"] = None
    row["component_breakdown"] = None
    row["matched_signals"] = []
    row["reasoning"] = INCOMPLETE_REASON
    row["scored_at"] = rc.scored_at.isoformat() if rc and rc.scored_at else None
    row["score_status"] = "insufficient_data"
    return row


def list_scored_candidates(db: Session, role_id: uuid.UUID) -> list[dict[str, Any]]:
    """Ranked scored candidates only (complete profiles with a real score)."""
    rows = db.execute(
        select(Candidate, RoleCandidate)
        .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
        .where(
            RoleCandidate.role_id == role_id,
            RoleCandidate.scored_at.is_not(None),
            RoleCandidate.total_score.is_not(None),
            Candidate.is_complete_profile.is_(True),
        )
        .order_by(RoleCandidate.total_score.desc().nullslast())
    ).all()
    return [_scored_card(cand, rc) for cand, rc in rows]


def list_score_payload(db: Session, role_id: uuid.UUID) -> dict[str, Any]:
    """Scored cards + incomplete skip list for UI."""
    scored = list_scored_candidates(db, role_id)
    incomplete_pairs = list_incomplete_for_role(db, role_id)
    incomplete = [_incomplete_card(cand, rc) for cand, rc in incomplete_pairs]
    return {
        "candidates": scored,
        "count": len(scored),
        "skipped_incomplete": len(incomplete),
        "incomplete_candidates": incomplete,
    }


def _load_role_candidates_for_scoring(
    db: Session, role_id: uuid.UUID
) -> list[tuple[Candidate, RoleCandidate]]:
    return list(
        db.execute(
            select(Candidate, RoleCandidate)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(RoleCandidate.role_id == role_id)
        ).all()
    )


def _call_scoring_api(jd_text: str, candidates_payload: list[dict]) -> list[dict]:
    settings = get_settings()
    url = f"{settings.scoring_api_url}/score"
    try:
        resp = requests.post(
            url,
            json={"jd_text": jd_text, "candidates": candidates_payload},
            timeout=SCORE_TIMEOUT_SECONDS,
        )
    except requests.Timeout as e:
        logger.warning("scoring API timeout url=%s", url)
        raise ScoringTransientError(SCORE_SLOW_MESSAGE) from e
    except requests.RequestException as e:
        logger.warning("scoring API unreachable url=%s err=%s", url, e)
        raise ScoringTransientError(SCORE_UNREACHABLE_MESSAGE) from e

    if resp.status_code >= 500:
        logger.warning(
            "scoring API %s status=%s body=%s",
            url,
            resp.status_code,
            resp.text[:300],
        )
        raise ScoringTransientError(
            "Scoring service is temporarily unavailable "
            "(it may still be starting up). Please try again in a minute."
        )

    if resp.status_code >= 400:
        detail = "Scoring request failed"
        try:
            body = resp.json()
            if isinstance(body.get("detail"), str):
                detail = body["detail"]
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(detail)

    data = resp.json()
    cards = data.get("cards")
    if not isinstance(cards, list):
        raise ValueError("Scoring service returned an unexpected response")
    return cards


def score_role(db: Session, role: Role) -> dict[str, Any]:
    """Score complete candidates; skip thin Short stubs with a clear status."""
    jd_text = (role.jd_text or "").strip()
    if not jd_text:
        raise ValueError(
            "Job description is not set for this role — save a JD before scoring."
        )

    pairs = _load_role_candidates_for_scoring(db, role.id)
    if not pairs:
        raise ValueError("No sourced candidates for this role — pull candidates first.")

    complete = [(c, rc) for c, rc in pairs if c.is_complete_profile]
    incomplete = [(c, rc) for c, rc in pairs if not c.is_complete_profile]
    now = datetime.now(timezone.utc)

    # Clear any prior fabricated scores on incomplete rows.
    for cand, rc in incomplete:
        rc.total_score = None
        rc.component_breakdown = None
        rc.matched_signals = []
        rc.reasoning = INCOMPLETE_REASON
        rc.scored_at = None
        logger.info(
            "score_role skipping incomplete candidate_id=%s url=%s",
            cand.id,
            cand.linkedin_url,
        )

    if not complete:
        role.updated_at = now
        db.commit()
        skipped = len(incomplete)
        return {
            "candidates": [],
            "count": 0,
            "skipped_incomplete": skipped,
            "incomplete_candidates": [
                _incomplete_card(c, rc) for c, rc in incomplete
            ],
            "summary": (
                f"{skipped} candidates skipped — incomplete profile data "
                "(no complete profiles to score)."
            ),
        }

    payload = [
        {
            "candidate_id": str(cand.id),
            "raw_profile": cand.raw_profile or {},
        }
        for cand, _rc in complete
    ]

    logger.info(
        "score_role sending %s complete profiles (skipping %s incomplete)",
        len(payload),
        len(incomplete),
    )
    cards = _call_scoring_api(jd_text, payload)
    by_id = {str(cand.id): (cand, rc) for cand, rc in complete}

    for card in cards:
        cid = str(card.get("candidate_id") or "")
        pair = by_id.get(cid)
        if not pair:
            continue
        _cand, rc = pair
        score_val = card.get("total_score")
        rc.total_score = Decimal(str(score_val)) if score_val is not None else None
        rc.component_breakdown = card.get("component_breakdown")
        signals = card.get("matched_signals") or []
        rc.matched_signals = [str(s) for s in signals]
        rc.reasoning = card.get("reasoning")
        rc.scored_at = now

    role.updated_at = now
    db.commit()

    result = list_score_payload(db, role.id)
    skipped = result["skipped_incomplete"]
    if skipped:
        result["summary"] = (
            f"Scored {result['count']}; {skipped} candidates skipped — "
            "incomplete profile data."
        )
    else:
        result["summary"] = f"Scored {result['count']} candidates."
    return result


def save_role_jd(db: Session, role: Role, jd_text: str) -> Role:
    text = (jd_text or "").strip()
    if not text:
        raise ValueError("jd_text is required")
    role.jd_text = text
    role.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(role)
    return role
