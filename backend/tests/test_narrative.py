"""Tests for JD-hash narrative caching (no Claude calls)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import narrative as narrative_service


def test_compute_jd_hash_stable_for_parsed():
    role = SimpleNamespace(
        jd_parsed={"role": "HR Assistant", "company": {"name": "Acme"}},
        jd_text='{"role": "HR Assistant"}',
    )
    a = narrative_service.compute_jd_hash(role)
    b = narrative_service.compute_jd_hash(role)
    assert a == b
    assert a.startswith("parsed:")


def test_compute_jd_hash_changes_when_parsed_changes():
    role_a = SimpleNamespace(
        jd_parsed={"role": "HR Assistant", "company": {"name": "Acme"}},
        jd_text=None,
    )
    role_b = SimpleNamespace(
        jd_parsed={"role": "HR Assistant", "company": {"name": "Beta"}},
        jd_text=None,
    )
    assert narrative_service.compute_jd_hash(role_a) != narrative_service.compute_jd_hash(
        role_b
    )


def test_compute_jd_hash_text_mode():
    role = SimpleNamespace(jd_parsed=None, jd_text="  Hire an HR Assistant in Dubai  ")
    h = narrative_service.compute_jd_hash(role)
    assert h.startswith("text:")
    # Whitespace stripped — same content same hash
    role2 = SimpleNamespace(jd_parsed=None, jd_text="Hire an HR Assistant in Dubai")
    assert h == narrative_service.compute_jd_hash(role2)


def test_narrate_role_skips_when_all_current():
    jd_hash = "parsed:abc"
    role = SimpleNamespace(
        id="role-1",
        jd_parsed={"role": "X", "company": {"name": "Y"}},
        jd_text=None,
        updated_at=None,
    )
    cand = SimpleNamespace(id="c1", raw_profile={}, is_complete_profile=True)
    rc = SimpleNamespace(
        scored_at=datetime.now(timezone.utc),
        total_score=0.5,
        narrative_generated_at=datetime.now(timezone.utc),
        narrative_jd_hash=jd_hash,
        component_breakdown={},
        matched_signals=[],
    )

    db = MagicMock()
    db.execute.return_value.all.return_value = [(cand, rc)]

    with (
        patch.object(narrative_service, "role_has_jd", return_value=True),
        patch.object(narrative_service, "compute_jd_hash", return_value=jd_hash),
        patch.object(narrative_service, "_call_narrate_api") as call_api,
    ):
        result = narrative_service.narrate_role(db, role)

    call_api.assert_not_called()
    assert result["generated"] == 0
    assert result["skipped_already_current"] == 1
    assert result["failed"] == 0
    assert "already have current narratives" in result["summary"]


def test_narrate_role_calls_api_when_hash_mismatch():
    role = SimpleNamespace(
        id="role-1",
        jd_parsed={"role": "X", "company": {"name": "Y"}},
        jd_text=None,
        updated_at=None,
    )
    cand = SimpleNamespace(id="c1", raw_profile={"about": "HR"}, is_complete_profile=True)
    rc = SimpleNamespace(
        scored_at=datetime.now(timezone.utc),
        total_score=0.5,
        narrative_generated_at=datetime.now(timezone.utc),
        narrative_jd_hash="parsed:old",
        component_breakdown={"skill": 0.8},
        matched_signals=["Payroll"],
        summary_text="old",
        assessment_text="old",
    )

    db = MagicMock()
    db.execute.return_value.all.return_value = [(cand, rc)]

    with (
        patch.object(narrative_service, "role_has_jd", return_value=True),
        patch.object(narrative_service, "compute_jd_hash", return_value="parsed:new"),
        patch.object(
            narrative_service,
            "_call_narrate_api",
            return_value=[
                {
                    "candidate_id": "c1",
                    "summary": "Fresh summary about HR work.",
                    "assessment": "Strong payroll fit for this JD.",
                    "error": None,
                }
            ],
        ) as call_api,
    ):
        result = narrative_service.narrate_role(db, role)

    call_api.assert_called_once()
    args, kwargs = call_api.call_args
    assert kwargs["jd_parsed_or_text"] == role.jd_parsed
    assert args[0][0]["candidate_id"] == "c1"
    assert result["generated"] == 1
    assert result["skipped_already_current"] == 0
    assert result["failed"] == 0
    assert rc.summary_text.startswith("Fresh summary")
    assert rc.assessment_text.startswith("Strong payroll")
    assert rc.narrative_jd_hash == "parsed:new"
    assert rc.narrative_generated_at is not None
    db.commit.assert_called_once()
