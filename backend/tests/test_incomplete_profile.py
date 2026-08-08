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


def test_linkedin_member_stem_only_for_acw_aco_never_vanity_slugs():
    from app.services.pull_batch import _linkedin_member_stem

    short_id = "ACwAADBdtwoB-gklyGL5mrQ3JXsvLE5_FUyR8kw"
    full_id = "ACoAADBdtwoBFjcvcNOvf4QuK8JXnst1YbEZTbA"
    assert _linkedin_member_stem(short_id) == "DBdtwoB"
    assert _linkedin_member_stem(full_id) == "DBdtwoB"
    assert _linkedin_member_stem(f"https://www.linkedin.com/in/{short_id}") == (
        "DBdtwoB"
    )

    # Vanity slugs must never yield a stem (identity-collision bug).
    assert _linkedin_member_stem(
        "https://www.linkedin.com/in/abdullahkhere"
    ) is None
    assert _linkedin_member_stem(
        "https://www.linkedin.com/in/abdullah-al-zadjali-2553b7275"
    ) is None
    assert _linkedin_member_stem("ashika-s-kumar-00a9041a7") is None


def test_resolve_matches_acw_short_to_aco_full_via_stem():
    from app.services.pull_batch import (
        _index_full_profiles,
        _linkedin_member_stem,
    )

    short_id = "ACwAADBdtwoB-gklyGL5mrQ3JXsvLE5_FUyR8kw"
    full_id = "ACoAADBdtwoBFjcvcNOvf4QuK8JXnst1YbEZTbA"
    assert _linkedin_member_stem(short_id) == _linkedin_member_stem(full_id)

    short = {
        "id": short_id,
        "linkedinUrl": f"https://www.linkedin.com/in/{short_id}",
        "currentPositions": [],
    }
    full = {
        "id": full_id,
        "linkedinUrl": "https://www.linkedin.com/in/ashika-s-kumar-00a9041a7",
        "headline": "HR Ops",
        "experience": [{"position": "HR"}],
        "skills": [{"name": "Onboarding"}],
        "firstName": "Ashika",
    }
    by_url: dict = {}
    by_id: dict = {}
    by_stem: dict = {}
    _index_full_profiles([full], by_url, by_id, by_stem)
    # Vanity slug must not be indexed as a stem key.
    assert "ashika-" not in by_stem
    assert _linkedin_member_stem(full_id) in by_stem
    url = short["linkedinUrl"]
    got = _resolve_full_profile(
        url,
        by_url=by_url,
        by_id=by_id,
        short_by_url={url: short},
        by_stem=by_stem,
    )
    assert got is full


def test_resolve_does_not_cross_match_vanity_slug_prefix_collision():
    """abdullahkhere vs abdullah-al-zadjali share prefix 'abdulla' — must not merge."""
    from app.services.pull_batch import _index_full_profiles, _resolve_full_profile

    short = {
        "id": "ACwAAEMoH6EBxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAEMoH6EBxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "firstName": "Abdullah",
        "currentPositions": [],
    }
    # Wrong person: Sales Engineer vanity slug that collides on first-7 'abdulla'
    wrong_full = {
        "id": "ACoAABqhTg4Byyyyyyyyyyyyyyyyyyyyyyyyyy",
        "linkedinUrl": "https://www.linkedin.com/in/abdullahkhere",
        "headline": "Sales Engineer",
        "experience": [{"position": "Sales Engineer"}],
        "firstName": "Abdullah",
        "lastName": "Khan",
    }
    by_url: dict = {}
    by_id: dict = {}
    by_stem: dict = {}
    _index_full_profiles([wrong_full], by_url, by_id, by_stem)
    got = _resolve_full_profile(
        short["linkedinUrl"],
        by_url=by_url,
        by_id=by_id,
        short_by_url={short["linkedinUrl"]: short},
        by_stem=by_stem,
    )
    assert got is None


def test_upsert_updates_existing_when_passed_even_if_url_differs():
    from app.services.pull_batch import _upsert_candidate

    short_url = "https://www.linkedin.com/in/ACwAADBdtwoB-gklyGL5mrQ3JXsvLE5_FUyR8kw"
    existing = MagicMock()
    existing.id = uuid.uuid4()
    existing.linkedin_url = short_url
    existing.is_complete_profile = False
    existing.first_name = "Ashika"
    existing.last_name = "S. Kumar"
    existing.headline = ""
    existing.current_title = "HR"
    existing.current_company = None
    existing.location = "Dubai"
    existing.top_skills = ""
    existing.raw_profile = {"id": "ACwAADBdtwoB-gklyGL5mrQ3JXsvLE5_FUyR8kw"}

    full = {
        "id": "ACoAADBdtwoBFjcvcNOvf4QuK8JXnst1YbEZTbA",
        "linkedinUrl": "https://www.linkedin.com/in/ashika-s-kumar-00a9041a7",
        "firstName": "Ashika",
        "lastName": "S. Kumar",
        "headline": "HR Ops",
        "experience": [{"position": "HR"}],
        "skills": [{"name": "Onboarding"}],
    }
    display = {
        "firstName": "Ashika",
        "lastName": "S. Kumar",
        "headline": "HR Ops",
        "current_title": "HR Ops",
        "current_company": "Vantage",
        "location": "Dubai",
        "linkedinUrl": full["linkedinUrl"],
        "topSkills": "Onboarding",
    }

    db = MagicMock()
    # No conflict on slug URL
    db.execute.return_value.scalar_one_or_none.return_value = None

    out = _upsert_candidate(
        db, full, display, is_complete=True, existing=existing
    )
    assert out is existing
    assert existing.is_complete_profile is True
    assert existing.headline == "HR Ops"
    assert existing.raw_profile is full
    # Migrated to public slug when no conflict
    assert existing.linkedin_url == full["linkedinUrl"]
    db.flush.assert_called()


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
    role.jd_parsed = None
    role.updated_at = None
    db = MagicMock()

    with patch.object(
        scoring_service,
        "_load_role_candidates_for_scoring",
        return_value=[(complete, rc_c), (incomplete, rc_i)],
    ), patch.object(
        scoring_service,
        "_call_scoring_api",
        return_value=(
            [
                {
                    "candidate_id": str(complete.id),
                    "total_score": 0.8,
                    "component_breakdown": {},
                    "matched_signals": [],
                    "reasoning": "ok",
                }
            ],
            "llm",
        ),
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
    sent = api.call_args[0][0]
    assert len(sent) == 1
    assert sent[0]["candidate_id"] == str(complete.id)
    assert rc_i.total_score is None
    assert rc_i.reasoning == INCOMPLETE_REASON
    assert rc_i.scored_at is None
    assert result["skipped_incomplete"] == 1
    assert "incomplete" in (result.get("summary") or "").lower()


def test_score_role_sends_all_complete_including_unscored():
    """Complete-but-never-scored profiles must be included in the scoring API payload."""
    scored = MagicMock()
    scored.id = uuid.uuid4()
    scored.is_complete_profile = True
    scored.raw_profile = {"experience": [{}], "skills": [{"name": "X"}]}
    scored.linkedin_url = "https://li/scored"

    never_scored = MagicMock()
    never_scored.id = uuid.uuid4()
    never_scored.is_complete_profile = True
    never_scored.raw_profile = {"experience": [{}], "skills": [{"name": "Y"}]}
    never_scored.linkedin_url = "https://li/pending"

    incomplete = MagicMock()
    incomplete.id = uuid.uuid4()
    incomplete.is_complete_profile = False
    incomplete.raw_profile = {}
    incomplete.linkedin_url = "https://li/thin"
    incomplete.first_name = "Thin"
    incomplete.last_name = "P"
    incomplete.headline = ""
    incomplete.current_title = ""
    incomplete.current_company = ""
    incomplete.location = ""
    incomplete.top_skills = ""

    rc_scored = MagicMock()
    rc_pending = MagicMock()
    rc_incomplete = MagicMock()
    role = MagicMock()
    role.id = uuid.uuid4()
    role.jd_text = "HR Assistant"
    role.jd_parsed = None
    role.updated_at = None
    db = MagicMock()

    with patch.object(
        scoring_service,
        "_load_role_candidates_for_scoring",
        return_value=[
            (scored, rc_scored),
            (never_scored, rc_pending),
            (incomplete, rc_incomplete),
        ],
    ), patch.object(
        scoring_service,
        "_call_scoring_api",
        return_value=(
            [
                {
                    "candidate_id": str(scored.id),
                    "total_score": 0.7,
                    "component_breakdown": {},
                    "matched_signals": [],
                    "reasoning": "ok",
                },
                {
                    "candidate_id": str(never_scored.id),
                    "total_score": 0.5,
                    "component_breakdown": {},
                    "matched_signals": [],
                    "reasoning": "ok",
                },
            ],
            "llm",
        ),
    ) as api, patch.object(
        scoring_service,
        "list_score_payload",
        return_value={
            "ranked": [{"first_name": "A"}, {"first_name": "B"}],
            "candidates": [{"first_name": "A"}, {"first_name": "B"}],
            "not_yet_scored": [],
            "not_yet_scored_count": 0,
            "count": 2,
            "skipped_incomplete": 1,
            "incomplete_candidates": [{"first_name": "Thin"}],
        },
    ):
        scoring_service.score_role(db, role)

    sent = api.call_args[0][0]
    sent_ids = {row["candidate_id"] for row in sent}
    assert sent_ids == {str(scored.id), str(never_scored.id)}
    assert str(incomplete.id) not in sent_ids


def test_list_score_payload_three_buckets():
    """GET scores payload exposes ranked / not_yet_scored / incomplete."""
    role_id = uuid.uuid4()

    ranked_cand = MagicMock()
    ranked_cand.id = uuid.uuid4()
    ranked_cand.is_complete_profile = True
    ranked_cand.first_name = "Ranked"
    ranked_cand.last_name = "One"
    ranked_cand.linkedin_url = "https://li/r"
    ranked_cand.headline = "HR"
    ranked_cand.current_title = "HR"
    ranked_cand.current_company = "Co"
    ranked_cand.location = "DXB"
    ranked_cand.top_skills = "X"
    ranked_cand.enrich_retry_count = 0

    pending_cand = MagicMock()
    pending_cand.id = uuid.uuid4()
    pending_cand.is_complete_profile = True
    pending_cand.first_name = "Pending"
    pending_cand.last_name = "Two"
    pending_cand.linkedin_url = "https://li/p"
    pending_cand.headline = "HR"
    pending_cand.current_title = "Coordinator"
    pending_cand.current_company = "Acme"
    pending_cand.location = "DXB"
    pending_cand.top_skills = "Y"
    pending_cand.enrich_retry_count = 0

    thin_cand = MagicMock()
    thin_cand.id = uuid.uuid4()
    thin_cand.is_complete_profile = False
    thin_cand.first_name = "Thin"
    thin_cand.last_name = "Three"
    thin_cand.linkedin_url = "https://li/t"
    thin_cand.headline = ""
    thin_cand.current_title = "Eng"
    thin_cand.current_company = "Co"
    thin_cand.location = "DXB"
    thin_cand.top_skills = ""
    thin_cand.enrich_retry_count = 1

    rc_ranked = MagicMock()
    rc_ranked.total_score = 0.8
    rc_ranked.component_breakdown = {}
    rc_ranked.matched_signals = []
    rc_ranked.reasoning = "ok"
    rc_ranked.summary_text = None
    rc_ranked.assessment_text = None
    rc_ranked.narrative_generated_at = None
    rc_ranked.narrative_jd_hash = None
    rc_ranked.scored_at = MagicMock()
    rc_ranked.scored_at.isoformat.return_value = "2026-08-06T11:07:33+00:00"
    rc_ranked.scoring_mode = "parsed"
    rc_ranked.review_status = "reviewing"

    rc_pending = MagicMock()
    rc_pending.scored_at = None
    rc_pending.pulled_at = MagicMock()

    rc_thin = MagicMock()
    rc_thin.scored_at = None

    db = MagicMock()

    def fake_execute(stmt):
        # Distinguish by inspecting compiled whereclause string-ish; simpler:
        # return based on call order via side_effect list instead.
        raise AssertionError("use side_effect")

    exec_ranked = MagicMock()
    exec_ranked.all.return_value = [(ranked_cand, rc_ranked)]
    exec_pending = MagicMock()
    exec_pending.all.return_value = [(pending_cand, rc_pending)]
    exec_thin = MagicMock()
    exec_thin.all.return_value = [(thin_cand, rc_thin)]
    db.execute.side_effect = [exec_ranked, exec_pending, exec_thin]

    with patch.object(
        scoring_service,
        "list_incomplete_for_role",
        return_value=[(thin_cand, rc_thin)],
    ):
        # list_score_payload calls list_scored, list_not_yet, then list_incomplete
        # Override the two list helpers to avoid SQLAlchemy select complexity.
        with patch.object(
            scoring_service,
            "list_scored_candidates",
            return_value=[scoring_service._scored_card(ranked_cand, rc_ranked)],
        ), patch.object(
            scoring_service,
            "list_not_yet_scored_candidates",
            return_value=[scoring_service._not_yet_scored_card(pending_cand)],
        ):
            result = scoring_service.list_score_payload(db, role_id)

    assert len(result["ranked"]) == 1
    assert result["ranked"][0]["first_name"] == "Ranked"
    assert result["candidates"] is result["ranked"] or result["candidates"] == result["ranked"]
    assert len(result["not_yet_scored"]) == 1
    assert result["not_yet_scored"][0]["first_name"] == "Pending"
    assert result["not_yet_scored"][0]["score_status"] == "not_yet_scored"
    assert "total_score" not in result["not_yet_scored"][0]
    assert len(result["incomplete_candidates"]) == 1
    assert result["incomplete_candidates"][0]["first_name"] == "Thin"
    assert result["count"] == 1
    assert result["skipped_incomplete"] == 1
    assert result["not_yet_scored_count"] == 1


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
    cand.enrich_retry_count = 0
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
    pair = (cand, MagicMock())

    with patch.object(
        pb, "list_incomplete_for_role", return_value=[pair]
    ), patch.object(
        pb, "list_retryable_incomplete_for_role", return_value=[pair]
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


def test_retry_incomplete_skips_exhausted_without_apify_call():
    from app.services import pull_batch as pb

    role_id = uuid.uuid4()
    role = MagicMock()
    role.id = role_id
    role.slug = "hr_assistant"
    role.retrieval = {"pool_cap": 10, "profileScraperMode": "Full"}
    role.last_page = 2
    role.effective_actor_input = {"dropped_keys": [], "actor_input": {}}

    cand = MagicMock()
    cand.id = uuid.uuid4()
    cand.linkedin_url = "https://www.linkedin.com/in/ACwAAstuck"
    cand.raw_profile = {}
    cand.is_complete_profile = False
    cand.enrich_retry_count = pb.MAX_ENRICH_RETRY_ATTEMPTS
    cand.first_name = "SIGNEY"
    cand.last_name = "A."
    cand.headline = None
    cand.current_title = "Assistant Manager"
    cand.current_company = None
    cand.location = "Dubai"
    cand.top_skills = None

    db = MagicMock()
    db.get.return_value = role
    pair = (cand, MagicMock())

    with patch.object(
        pb, "list_incomplete_for_role", return_value=[pair]
    ), patch.object(
        pb, "list_retryable_incomplete_for_role", return_value=[]
    ), patch.object(pb, "fetch_profiles") as fetch:
        result = pb.retry_incomplete_profiles(db, role_id)

    fetch.assert_not_called()
    assert result["upgraded"] == 0
    assert result["retryable"] == 0
    assert result["exhausted"] == 1
    assert "failed after" in result["summary"]
    assert result["candidates"][0]["enrich_status"] == "enrich_failed"


def test_retry_incomplete_increments_count_on_miss():
    from app.services import pull_batch as pb

    role_id = uuid.uuid4()
    role = MagicMock()
    role.id = role_id
    role.slug = "hr_assistant"
    role.retrieval = {
        "currentJobTitles": ["HR Assistant"],
        "location": "United Arab Emirates",
        "pool_cap": 10,
        "profileScraperMode": "Full",
    }
    role.last_page = 2
    role.effective_actor_input = {
        "dropped_keys": [],
        "actor_input": {
            "locations": ["United Arab Emirates"],
            "currentJobTitles": ["HR Assistant"],
        },
    }

    cand = MagicMock()
    cand.id = uuid.uuid4()
    cand.linkedin_url = "https://www.linkedin.com/in/ACwAAmiss"
    cand.raw_profile = {
        "id": "1",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAmiss",
    }
    cand.is_complete_profile = False
    cand.enrich_retry_count = 2
    cand.first_name = "Riswana"
    cand.last_name = "Sathar"
    cand.headline = None
    cand.current_title = "HR"
    cand.current_company = None
    cand.location = "Dubai"
    cand.top_skills = None

    db = MagicMock()
    db.get.return_value = role
    batch = MagicMock()
    batch.id = uuid.uuid4()
    pair = (cand, MagicMock())

    with patch.object(
        pb, "list_incomplete_for_role", return_value=[pair]
    ), patch.object(
        pb, "list_retryable_incomplete_for_role", return_value=[pair]
    ), patch.object(
        pb, "compile_retrieval", return_value={"locations": ["United Arab Emirates"]}
    ), patch.object(
        # Non-empty but unmatched — avoids persisted-filter rediscover path.
        pb,
        "fetch_profiles",
        return_value=(
            [
                {
                    "id": "other",
                    "linkedinUrl": "https://www.linkedin.com/in/someone-else",
                    "experience": [{"position": "HR"}],
                }
            ],
            "SUCCEEDED",
            "run-miss",
        ),
    ), patch.object(
        pb, "_retry_full_enrich_for_missing", return_value=("SUCCEEDED", "run-miss")
    ), patch.object(
        pb, "_next_batch_number", return_value=1
    ), patch.object(
        pb, "_log_apify_call", return_value=batch
    ):
        result = pb.retry_incomplete_profiles(db, role_id)

    assert cand.enrich_retry_count == 3
    assert result["upgraded"] == 0
    assert result["exhausted"] == 1
    assert result["retryable"] == 0
    assert result["candidates"][0]["enrich_status"] == "enrich_failed"


def test_enrich_status_helpers():
    from app.services.pull_batch import (
        MAX_ENRICH_RETRY_ATTEMPTS,
        enrich_status,
        is_enrich_retry_exhausted,
    )

    complete = MagicMock(is_complete_profile=True, enrich_retry_count=0)
    assert enrich_status(complete) == "complete"

    thin = MagicMock(is_complete_profile=False, enrich_retry_count=1)
    assert enrich_status(thin) == "needs_re_pull"
    assert is_enrich_retry_exhausted(thin) is False

    done = MagicMock(
        is_complete_profile=False, enrich_retry_count=MAX_ENRICH_RETRY_ATTEMPTS
    )
    assert enrich_status(done) == "enrich_failed"
    assert is_enrich_retry_exhausted(done) is True

def test_force_retry_single_exhausted_candidate_calls_apify():
    from app.services import pull_batch as pb

    role_id = uuid.uuid4()
    cand_id = uuid.uuid4()
    role = MagicMock()
    role.id = role_id
    role.slug = "hr_assistant"
    role.retrieval = {
        "currentJobTitles": ["HR Assistant"],
        "location": "United Arab Emirates",
        "pool_cap": 10,
        "profileScraperMode": "Full",
    }
    role.last_page = 2
    role.effective_actor_input = {
        "dropped_keys": ["searchQuery"],
        "actor_input": {
            "locations": ["United Arab Emirates"],
            "currentJobTitles": ["HR Assistant"],
        },
    }
    role.updated_at = None

    cand = MagicMock()
    cand.id = cand_id
    cand.linkedin_url = "https://www.linkedin.com/in/ACwAAstuck"
    cand.raw_profile = {
        "id": "99",
        "linkedinUrl": "https://www.linkedin.com/in/ACwAAstuck",
    }
    cand.is_complete_profile = False
    cand.enrich_retry_count = pb.MAX_ENRICH_RETRY_ATTEMPTS
    cand.first_name = "SIGNEY"
    cand.last_name = "A."
    cand.headline = None
    cand.current_title = "Assistant"
    cand.current_company = None
    cand.location = "Dubai"
    cand.top_skills = None

    rc = MagicMock()
    rc.manually_ignored = False
    rc.pulled_at = None
    rc.batch_id = None

    db = MagicMock()
    db.get.return_value = role

    with patch.object(
        pb, "_get_role_candidate_pair", return_value=(cand, rc)
    ), patch.object(
        pb, "compile_retrieval", return_value={"currentJobTitles": ["HR Assistant"]}
    ), patch.object(
        pb,
        "_resolve_retry_effective_input",
        return_value=(
            {"locations": ["United Arab Emirates"], "currentJobTitles": ["HR Assistant"]},
            ["searchQuery"],
            False,
        ),
    ), patch.object(
        pb, "fetch_profiles", return_value=([], "SUCCEEDED", "run-1")
    ) as fetch, patch.object(
        pb, "_retry_full_enrich_for_missing", return_value=(None, None)
    ), patch.object(
        pb, "_next_batch_number", return_value=1
    ), patch.object(
        pb, "_log_apify_call", return_value=MagicMock(id=uuid.uuid4())
    ):
        result = pb.retry_incomplete_profiles(
            db, role_id, candidate_ids=[cand_id], force=True
        )

    fetch.assert_called()
    assert result["upgraded"] == 0
    assert cand.enrich_retry_count == pb.MAX_ENRICH_RETRY_ATTEMPTS
    assert result["candidates"][0]["enrich_status"] == "enrich_failed"


def test_set_manually_ignored_toggles_role_scoped_flag():
    from app.services import pull_batch as pb

    role_id = uuid.uuid4()
    cand_id = uuid.uuid4()
    cand = MagicMock()
    cand.id = cand_id
    cand.linkedin_url = "https://li/x"
    cand.is_complete_profile = False
    cand.enrich_retry_count = 3
    cand.first_name = "Riswana"
    cand.last_name = "Sathar"
    cand.headline = None
    cand.current_title = "HR"
    cand.current_company = None
    cand.location = "Dubai"
    cand.top_skills = None

    rc = MagicMock()
    rc.manually_ignored = False
    rc.pulled_at = None
    rc.batch_id = None

    db = MagicMock()
    with patch.object(pb, "_get_role_candidate_pair", return_value=(cand, rc)):
        out = pb.set_manually_ignored(db, role_id, cand_id, True)

    assert rc.manually_ignored is True
    assert out["manually_ignored"] is True
    assert out["candidate"]["first_name"] == "Riswana"
    db.commit.assert_called()
