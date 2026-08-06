"""Unit tests for JD paste classification (plain text vs scoring brief JSON)."""

from __future__ import annotations

import json

import pytest

from app.services.scoring import classify_jd_paste, role_has_jd


def test_plain_text_sets_no_parsed():
    text, parsed = classify_jd_paste(
        "We need an HR Assistant in Dubai with WPS and visa experience."
    )
    assert parsed is None
    assert "HR Assistant" in text


def test_valid_brief_json_stored_pretty():
    brief = {
        "role": "HR Assistant",
        "company": {"name": "Prime AC"},
        "responsibilities": ["Onboarding", "Payroll support"],
        "skills": [
            {"skill": "WPS", "priority": "essential"},
            {"skill": "Visa processing", "priority": "important"},
        ],
    }
    # Paste as compact JSON — save should pretty-print
    text, parsed = classify_jd_paste(json.dumps(brief))
    assert parsed == brief
    assert text.startswith("{\n")
    assert '"role": "HR Assistant"' in text


def test_looks_like_json_but_invalid_syntax_rejected():
    with pytest.raises(ValueError, match="not valid JSON"):
        classify_jd_paste('{ "role": "HR Assistant", ')


def test_looks_like_json_but_missing_keys_rejected():
    with pytest.raises(ValueError, match="failed validation"):
        classify_jd_paste(json.dumps({"role": "HR Assistant", "company": {"name": "X"}}))


def test_looks_like_json_bad_skills_shape_rejected():
    brief = {
        "role": "HR Assistant",
        "company": {"name": "Prime AC"},
        "responsibilities": ["Onboarding"],
        "skills": ["WPS"],  # must be objects
    }
    with pytest.raises(ValueError, match="skills"):
        classify_jd_paste(json.dumps(brief))


def test_role_has_jd_with_parsed_only():
    class R:
        jd_text = None
        jd_parsed = {"role": "X"}

    assert role_has_jd(R()) is True


def test_role_has_jd_with_text_only():
    class R:
        jd_text = "plain jd"
        jd_parsed = None

    assert role_has_jd(R()) is True


def test_role_has_jd_empty():
    class R:
        jd_text = "  "
        jd_parsed = None

    assert role_has_jd(R()) is False
