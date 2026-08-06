"""Call the external Contra6 Scoring API and persist results on role_candidates."""

from __future__ import annotations

import json
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

_REQUIRED_BRIEF_KEYS = ("role", "company", "responsibilities", "skills")


class ScoringTransientError(Exception):
    """Timeout / connection failures talking to the scoring API."""

    def __init__(self, message: str = SCORE_SLOW_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


def role_has_jd(role: Role) -> bool:
    return bool((role.jd_text or "").strip()) or bool(role.jd_parsed)


def _looks_like_json_object(text: str) -> bool:
    return text.lstrip().startswith("{")


def _validate_scoring_brief(obj: Any) -> list[str]:
    """Return a list of human-readable problems; empty means valid enough to store."""
    errors: list[str] = []
    if not isinstance(obj, dict):
        return ["Scoring brief must be a JSON object."]

    for key in _REQUIRED_BRIEF_KEYS:
        if key not in obj:
            errors.append(f"Missing required field: {key}")

    if "role" in obj and not isinstance(obj["role"], str):
        errors.append("'role' must be a string")
    elif "role" in obj and not (obj["role"] or "").strip():
        errors.append("'role' must be a non-empty string")

    company = obj.get("company")
    if "company" in obj:
        if not isinstance(company, dict):
            errors.append("'company' must be an object with at least 'name'")
        elif not isinstance(company.get("name"), str) or not company.get("name", "").strip():
            errors.append("'company.name' must be a non-empty string")

    responsibilities = obj.get("responsibilities")
    if "responsibilities" in obj:
        if not isinstance(responsibilities, list) or not responsibilities:
            errors.append("'responsibilities' must be a non-empty array of strings")
        elif not all(isinstance(r, str) and r.strip() for r in responsibilities):
            errors.append("'responsibilities' entries must be non-empty strings")

    skills = obj.get("skills")
    if "skills" in obj:
        if not isinstance(skills, list) or not skills:
            errors.append("'skills' must be a non-empty array of objects")
        else:
            for i, sk in enumerate(skills):
                if not isinstance(sk, dict):
                    errors.append(f"skills[{i}] must be an object with 'skill' and 'priority'")
                    continue
                if not isinstance(sk.get("skill"), str) or not sk.get("skill", "").strip():
                    errors.append(f"skills[{i}].skill must be a non-empty string")
                if not isinstance(sk.get("priority"), str) or not sk.get("priority", "").strip():
                    errors.append(f"skills[{i}].priority must be a non-empty string")

    return errors


def classify_jd_paste(raw: str) -> tuple[str, dict | None]:
    """Classify pasted content as plain text or a scoring brief.

    Returns (display_text, jd_parsed_or_None).

    - Plain text (does not start with '{'): jd_parsed=None, display_text=raw.
    - Valid scoring-brief JSON: jd_parsed=dict, display_text=pretty JSON.
    - Looks like JSON but invalid: raises ValueError with a clear message.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("jd_text is required")

    if not _looks_like_json_object(text):
        return text, None

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            "Content looks like JSON (starts with '{') but is not valid JSON: "
            f"{e.msg} (line {e.lineno}, column {e.colno}). "
            "Fix the JSON, or paste plain job-description text instead."
        ) from e

    problems = _validate_scoring_brief(obj)
    if problems:
        raise ValueError(
            "Content looks like a scoring brief but failed validation: "
            + "; ".join(problems)
            + ". Required top-level keys: role, company, responsibilities, skills "
            "(each skill needs skill + priority)."
        )

    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    return pretty, obj


def _scored_card(cand: Candidate, rc: RoleCandidate) -> dict[str, Any]:
    row = _candidate_row(cand)
    row["candidate_id"] = str(cand.id)
    row["total_score"] = float(rc.total_score) if rc.total_score is not None else None
    row["component_breakdown"] = rc.component_breakdown
    row["matched_signals"] = list(rc.matched_signals or [])
    row["reasoning"] = rc.reasoning
    row["scored_at"] = rc.scored_at.isoformat() if rc.scored_at else None
    row["scoring_mode"] = getattr(rc, "scoring_mode", None)
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
    row["scoring_mode"] = None
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
    modes = {c.get("scoring_mode") for c in scored if c.get("scoring_mode")}
    scoring_mode = None
    if len(modes) == 1:
        scoring_mode = next(iter(modes))
    elif len(modes) > 1:
        scoring_mode = "mixed"
    return {
        "candidates": scored,
        "count": len(scored),
        "skipped_incomplete": len(incomplete),
        "incomplete_candidates": incomplete,
        "scoring_mode": scoring_mode,
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


def _call_scoring_api(
    candidates_payload: list[dict],
    *,
    jd_text: str | None = None,
    parsed_jd: dict | None = None,
) -> tuple[list[dict], str]:
    """POST /score. Returns (cards, scoring_mode)."""
    settings = get_settings()
    url = f"{settings.scoring_api_url}/score"
    body: dict[str, Any] = {"candidates": candidates_payload}
    if parsed_jd is not None:
        body["parsed_jd"] = parsed_jd
        body["jd_text"] = jd_text or "(scoring brief)"
    else:
        body["jd_text"] = jd_text or ""

    try:
        resp = requests.post(
            url,
            json=body,
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
            err_body = resp.json()
            if isinstance(err_body.get("detail"), str):
                detail = err_body["detail"]
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(detail)

    data = resp.json()
    cards = data.get("cards")
    if not isinstance(cards, list):
        raise ValueError("Scoring service returned an unexpected response")
    mode = data.get("scoring_mode")
    if mode not in ("parsed", "llm"):
        # Older scoring API without scoring_mode — infer from what we sent.
        mode = "parsed" if parsed_jd is not None else "llm"
    return cards, mode


def score_role(db: Session, role: Role) -> dict[str, Any]:
    """Score complete candidates; skip thin Short stubs with a clear status."""
    if not role_has_jd(role):
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
        rc.scoring_mode = None
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
            "scoring_mode": None,
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

    parsed = role.jd_parsed if isinstance(role.jd_parsed, dict) else None
    logger.info(
        "score_role sending %s complete profiles (skipping %s incomplete) "
        "has_parsed_jd=%s",
        len(payload),
        len(incomplete),
        parsed is not None,
    )
    if parsed is not None:
        cards, scoring_mode = _call_scoring_api(
            payload,
            jd_text="(scoring brief)",
            parsed_jd=parsed,
        )
    else:
        cards, scoring_mode = _call_scoring_api(
            payload,
            jd_text=(role.jd_text or "").strip(),
        )

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
        rc.scoring_mode = scoring_mode

    role.updated_at = now
    db.commit()

    result = list_score_payload(db, role.id)
    result["scoring_mode"] = scoring_mode
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
    display_text, parsed = classify_jd_paste(jd_text)
    role.jd_text = display_text
    role.jd_parsed = parsed
    role.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(role)
    return role
