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


def test_filter_snapshot_strips_run_keys():
    from app.services.pull_batch import _filter_snapshot

    snap = _filter_snapshot(
        {
            "searchQuery": "HVAC",
            "currentJobTitles": ["HR Assistant"],
            "startPage": 1,
            "takePages": 2,
            "maxItems": 25,
            "profileScraperMode": "Full",
        }
    )
    assert snap == {
        "searchQuery": "HVAC",
        "currentJobTitles": ["HR Assistant"],
    }


def test_dropped_actor_keys():
    from app.services.pull_batch import _dropped_actor_keys

    assert _dropped_actor_keys(
        {"searchQuery": "HVAC", "currentJobTitles": ["HR"]},
        {"currentJobTitles": ["HR"]},
    ) == ["searchQuery"]


def test_resolve_retry_uses_persisted_filters():
    from app.services.pull_batch import _resolve_retry_effective_input

    role = MagicMock()
    role.effective_actor_input = {
        "dropped_keys": ["searchQuery"],
        "actor_input": {
            "locations": ["United Arab Emirates"],
            "currentJobTitles": ["HR Assistant"],
            "functionIds": ["14"],
        },
    }
    compiled = {
        "searchQuery": "HVAC Ductwork MEP",
        "locations": ["United Arab Emirates"],
        "currentJobTitles": ["HR Assistant"],
        "functionIds": ["14"],
    }
    effective, dropped, used = _resolve_retry_effective_input(role, compiled)
    assert used is True
    assert dropped == ["searchQuery"]
    assert "searchQuery" not in effective
    assert effective["currentJobTitles"] == ["HR Assistant"]


def test_resolve_retry_runs_probe_with_relax_when_no_persist():
    from app.services import pull_batch as pb

    role = MagicMock()
    role.id = uuid.uuid4()
    role.effective_actor_input = None
    compiled = {
        "searchQuery": "HVAC Ductwork MEP",
        "locations": ["United Arab Emirates"],
        "currentJobTitles": ["HR Assistant"],
        "functionIds": ["14"],
        "maxItems": 25,
        "takePages": 1,
        "profileScraperMode": "Full",
    }
    relaxed = {
        "locations": ["United Arab Emirates"],
        "currentJobTitles": ["HR Assistant"],
        "functionIds": ["14"],
        "maxItems": 25,
        "takePages": 1,
        "profileScraperMode": "Full",
    }
    with patch.object(
        pb,
        "probe_with_relax",
        return_value=(10, [{"linkedinUrl": "https://li/x"}], relaxed),
    ) as probe:
        effective, dropped, used = pb._resolve_retry_effective_input(
            role, compiled
        )

    assert used is False
    assert dropped == ["searchQuery"]
    assert "searchQuery" not in effective
    probe.assert_called_once()
    assert role.effective_actor_input["dropped_keys"] == ["searchQuery"]
    assert "searchQuery" not in role.effective_actor_input["actor_input"]


def test_retry_incomplete_builds_full_input_without_search_query():
    from app.services import pull_batch as pb

    role_id = uuid.uuid4()
    role = MagicMock()
    role.id = role_id
    role.slug = "hr_assistant"
    role.retrieval = {
        "searchQuery": "HVAC Ductwork MEP",
        "currentJobTitles": ["HR Assistant"],
        "location": "United Arab Emirates",
        "functions": ["Human Resources"],
        "pool_cap": 25,
        "profileScraperMode": "Full",
    }
    role.last_page = 2
    role.effective_actor_input = None

    cand = MagicMock()
    cand.id = uuid.uuid4()
    cand.linkedin_url = "https://www.linkedin.com/in/ACwAAtest"
    cand.raw_profile = {
        "id": "99",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAtest",
        "currentPositions": [{"title": "HR Assistant"}],
    }
    cand.is_complete_profile = False
    cand.first_name = "Ashika"
    cand.last_name = "X"
    cand.headline = None
    cand.current_title = "HR Assistant"
    cand.current_company = None
    cand.location = "Dubai"
    cand.top_skills = None

    relaxed = {
        "locations": ["United Arab Emirates"],
        "currentJobTitles": ["HR Assistant"],
        "functionIds": ["14"],
        "maxItems": 25,
        "takePages": 1,
        "profileScraperMode": "Full",
    }
    full_profile = {
        "id": "99",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAtest",
        "headline": "HR Assistant",
        "experience": [{"position": "HR Assistant"}],
        "skills": [{"name": "Onboarding"}],
        "firstName": "Ashika",
        "lastName": "X",
    }

    db = MagicMock()
    db.get.return_value = role
    batch = MagicMock()
    batch.id = uuid.uuid4()

    with patch.object(
        pb, "list_incomplete_for_role", return_value=[(cand, MagicMock())]
    ), patch.object(
        pb,
        "compile_retrieval",
        return_value={
            "searchQuery": "HVAC Ductwork MEP",
            "locations": ["United Arab Emirates"],
            "currentJobTitles": ["HR Assistant"],
            "functionIds": ["14"],
            "maxItems": 25,
            "takePages": 1,
            "profileScraperMode": "Full",
        },
    ), patch.object(
        pb,
        "probe_with_relax",
        return_value=(10, [{"linkedinUrl": cand.linkedin_url}], relaxed),
    ), patch.object(
        pb, "fetch_profiles", return_value=([full_profile], "SUCCEEDED", "run1")
    ) as fetch, patch.object(
        pb, "_next_batch_number", return_value=1
    ), patch.object(
        pb, "_log_apify_call", return_value=batch
    ), patch.object(
        pb, "_upsert_candidate", return_value=cand
    ), patch.object(
        pb, "compact", return_value={"first_name": "Ashika"}
    ):
        result = pb.retry_incomplete_profiles(db, role_id)

    sent = fetch.call_args[0][0]
    assert "searchQuery" not in sent
    assert sent["profileScraperMode"] == "Full"
    assert sent["currentJobTitles"] == ["HR Assistant"]
    assert result["upgraded"] == 1
    assert result["still_incomplete"] == 0
    assert result["dropped_keys"] == ["searchQuery"]
