"""Generate and cache LLM narratives via the external scoring API /narrate."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Candidate, Role, RoleCandidate
from app.services.scoring import ScoringTransientError, role_has_jd

logger = logging.getLogger("sourcing.narrative")

NARRATE_TIMEOUT_SECONDS = 300
NARRATE_SLOW_MESSAGE = (
    "Narrative generation is taking longer than expected — the scoring service "
    "may be cold-starting. Please try again in a minute."
)
NARRATE_UNREACHABLE_MESSAGE = (
    "Could not reach the scoring service — please try again shortly."
)


def compute_jd_hash(role: Role) -> str:
    """Stable hash of the JD artifact that drives scoring for this role."""
    if isinstance(role.jd_parsed, dict):
        payload = json.dumps(
            role.jd_parsed,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prefix = "parsed:"
    else:
        payload = (role.jd_text or "").strip()
        prefix = "text:"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def _needs_narrative(rc: RoleCandidate, jd_hash: str) -> bool:
    if rc.narrative_generated_at is None:
        return True
    return (rc.narrative_jd_hash or "") != jd_hash


def _call_narrate_api(
    candidates_payload: list[dict[str, Any]],
    *,
    jd_parsed_or_text: dict | str,
) -> list[dict[str, Any]]:
    settings = get_settings()
    url = f"{settings.scoring_api_url}/narrate"
    body: dict[str, Any] = {
        "jd_parsed_or_text": jd_parsed_or_text,
        "candidates": candidates_payload,
    }

    try:
        resp = requests.post(url, json=body, timeout=NARRATE_TIMEOUT_SECONDS)
    except requests.Timeout as e:
        logger.warning("narrate API timeout url=%s", url)
        raise ScoringTransientError(NARRATE_SLOW_MESSAGE) from e
    except requests.RequestException as e:
        logger.warning("narrate API unreachable url=%s err=%s", url, e)
        raise ScoringTransientError(NARRATE_UNREACHABLE_MESSAGE) from e

    if resp.status_code >= 500:
        logger.warning(
            "narrate API %s status=%s body=%s",
            url,
            resp.status_code,
            resp.text[:300],
        )
        raise ScoringTransientError(
            "Scoring service is temporarily unavailable "
            "(it may still be starting up). Please try again in a minute."
        )

    if resp.status_code >= 400:
        detail = "Narrative request failed"
        try:
            err_body = resp.json()
            if isinstance(err_body.get("detail"), str):
                detail = err_body["detail"]
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(detail)

    data = resp.json()
    narratives = data.get("narratives")
    if not isinstance(narratives, list):
        raise ValueError("Narrative service returned an unexpected response")
    return narratives


def narrate_role(db: Session, role: Role) -> dict[str, Any]:
    """Generate narratives for scored candidates missing a current JD-hash cache."""
    if not role_has_jd(role):
        raise ValueError(
            "Job description is not set for this role — save a JD before generating narratives."
        )

    jd_hash = compute_jd_hash(role)
    pairs = list(
        db.execute(
            select(Candidate, RoleCandidate)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(
                RoleCandidate.role_id == role.id,
                RoleCandidate.scored_at.is_not(None),
                RoleCandidate.total_score.is_not(None),
                RoleCandidate.manually_ignored.is_(False),
                Candidate.is_complete_profile.is_(True),
            )
        ).all()
    )

    if not pairs:
        raise ValueError(
            "No scored candidates for this role — score candidates before generating narratives."
        )

    to_generate: list[tuple[Candidate, RoleCandidate]] = []
    skipped = 0
    for cand, rc in pairs:
        if _needs_narrative(rc, jd_hash):
            to_generate.append((cand, rc))
        else:
            skipped += 1

    if not to_generate:
        return {
            "generated": 0,
            "skipped_already_current": skipped,
            "failed": 0,
            "jd_hash": jd_hash,
            "summary": "All candidates already have current narratives for this JD",
        }

    payload = [
        {
            "candidate_id": str(cand.id),
            "raw_profile": cand.raw_profile or {},
            "component_breakdown": rc.component_breakdown,
            "matched_signals": list(rc.matched_signals or []),
        }
        for cand, rc in to_generate
    ]

    parsed = role.jd_parsed if isinstance(role.jd_parsed, dict) else None
    jd_parsed_or_text: dict | str
    if parsed is not None:
        jd_parsed_or_text = parsed
    else:
        jd_parsed_or_text = (role.jd_text or "").strip()

    logger.info(
        "narrate_role sending %s candidates (skipping %s current) has_parsed_jd=%s",
        len(payload),
        skipped,
        parsed is not None,
    )

    narratives = _call_narrate_api(payload, jd_parsed_or_text=jd_parsed_or_text)

    by_id = {str(cand.id): rc for cand, rc in to_generate}
    now = datetime.now(timezone.utc)
    generated = 0
    failed = 0

    for item in narratives:
        if not isinstance(item, dict):
            failed += 1
            continue
        cid = str(item.get("candidate_id") or "")
        rc = by_id.get(cid)
        if rc is None:
            continue
        err = item.get("error")
        summary = (item.get("summary") or "").strip() if item.get("summary") else ""
        assessment = (
            (item.get("assessment") or "").strip() if item.get("assessment") else ""
        )
        if err or not summary or not assessment:
            failed += 1
            logger.warning(
                "narrate_role skip write candidate_id=%s error=%s",
                cid,
                err or "empty fields",
            )
            continue
        rc.summary_text = summary
        rc.assessment_text = assessment
        rc.narrative_generated_at = now
        rc.narrative_jd_hash = jd_hash
        generated += 1

    role.updated_at = now
    db.commit()

    return {
        "generated": generated,
        "skipped_already_current": skipped,
        "failed": failed,
        "jd_hash": jd_hash,
        "summary": (
            f"Generated {generated}, {skipped} already current"
            + (f", {failed} failed" if failed else "")
        ),
    }
