"""Tests for Short-stub completeness flag and scoring skip."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.services.pull_batch import (
    INCOMPLETE_REASON,
    _is_complete_apify_profile,
    _resolve_full_profile,
)
from app.services import scoring as scoring_service


def test_short_stub_is_incomplete():
    short = {
        "firstName": "A",
        "currentPositions": [{"title": "HR Assistant"}],
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAxxx",
    }
    assert _is_complete_apify_profile(short) is False


def test_full_profile_is_complete():
    full = {
        "headline": "HR Assistant",
        "experience": [{"position": "HR Assistant"}],
        "skills": [{"name": "Onboarding"}],
    }
    assert _is_complete_apify_profile(full) is True


def test_resolve_prefers_full_by_id():
    short = {"id": "99", "linkedinUrl": "https://li/a", "currentPositions": []}
    full = {
        "id": "99",
        "linkedinUrl": "https://li/other-form",
        "experience": [{"position": "HR"}],
        "skills": [{"name": "X"}],
    }
    got = _resolve_full_profile(
        "https://li/a",
        by_url={},
        by_id={"99": full},
        short_by_url={"https://li/a": short},
    )
    assert got is full


def test_score_role_skips_incomplete_and_does_not_send_them():
    complete = MagicMock()
    complete.id = uuid.uuid4()
    complete.is_complete_profile = True
    complete.raw_profile = {"experience": [{}], "skills": [{"name": "X"}]}
    complete.first_name = "Maya"
    complete.last_name = "H"
    complete.linkedin_url = "https://li/maya"
    complete.headline = "HR"
    complete.current_title = "HR"
    complete.current_company = "Co"
    complete.location = "DXB"
    complete.top_skills = "X"

    incomplete = MagicMock()
    incomplete.id = uuid.uuid4()
    incomplete.is_complete_profile = False
    incomplete.raw_profile = {"currentPositions": []}
    incomplete.first_name = "Alex"
    incomplete.last_name = "C"
    incomplete.linkedin_url = "https://li/alex"
    incomplete.headline = ""
    incomplete.current_title = "Eng"
    incomplete.current_company = "Co"
    incomplete.location = "DXB"
    incomplete.top_skills = ""

    rc_c = MagicMock()
    rc_i = MagicMock()
    role = MagicMock()
    role.id = uuid.uuid4()
    role.jd_text = "HR Assistant"
    role.updated_at = None
    db = MagicMock()

    with patch.object(
        scoring_service,
        "_load_role_candidates_for_scoring",
        return_value=[(complete, rc_c), (incomplete, rc_i)],
    ), patch.object(
        scoring_service,
        "_call_scoring_api",
        return_value=[
            {
                "candidate_id": str(complete.id),
                "total_score": 0.8,
                "component_breakdown": {},
                "matched_signals": [],
                "reasoning": "ok",
            }
        ],
    ) as api, patch.object(
        scoring_service,
        "list_score_payload",
        return_value={
            "candidates": [{"first_name": "Maya"}],
            "count": 1,
            "skipped_incomplete": 1,
            "incomplete_candidates": [{"first_name": "Alex"}],
        },
    ):
        result = scoring_service.score_role(db, role)

    assert api.call_count == 1
    assert len(api.call_args[0][1]) == 1
    assert api.call_args[0][1][0]["candidate_id"] == str(complete.id)
    assert rc_i.total_score is None
    assert rc_i.reasoning == INCOMPLETE_REASON
    assert rc_i.scored_at is None
    assert result["skipped_incomplete"] == 1
    assert "incomplete" in (result.get("summary") or "").lower()
