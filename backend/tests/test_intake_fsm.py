"""Offline intake FSM smoke test — no Postgres / Apify / Anthropic required."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.chat import (
    _handle_intake,
    _set_progress,
    _progress,
)
from app.services.validation import clean_answer, resolve_function, resolve_years_tokens


def test_clean_answer_strips_bullets():
    assert clean_answer("· Sales") == "Sales"
    assert clean_answer("- Engineering") == "Engineering"
    assert clean_answer("* Human Resources") == "Human Resources"
    assert clean_answer("1. Operations") == "Operations"
    assert clean_answer("2) Finance") == "Finance"
    assert resolve_function("· Sales") == "sales"
    ok, bad = resolve_years_tokens("· 3 to 5 years, 6 to 10 years")
    assert ok == ["3 to 5 years", "6 to 10 years"] and not bad


def _fake_session(step="role_name", **extra):
    progress = {"step": step, **extra}
    s = SimpleNamespace(
        id=uuid.uuid4(),
        role_id=None,
        state="intake",
        intake_progress=progress,
        updated_at=None,
    )
    return s


def test_intake_advances_and_keeps_role_name():
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    db.get.return_value = None
    session = _fake_session()

    # role name
    with patch("app.services.chat._save_msg"), patch(
        "app.services.chat.llm.plausible_functions_for_role",
        return_value=["Human Resources", "Administrative", "Operations"],
    ):
        out = _handle_intake(db, session, "HR Assistant")
    assert session.intake_progress["step"] == "function"
    assert session.intake_progress["role_name"] == "HR Assistant"
    assert "HR Assistant" in out["assistant_message"]
    assert "**" not in out["assistant_message"]

    # function (with bullet paste)
    with patch("app.services.chat._save_msg"):
        out = _handle_intake(db, session, "· Human Resources")
    assert session.intake_progress["step"] == "years_of_experience"
    assert session.intake_progress["role_name"] == "HR Assistant"
    assert session.intake_progress["function_key"] == "human resources"
    assert "Years of experience" in out["assistant_message"]

    # years
    with patch("app.services.chat._save_msg"), patch(
        "app.services.chat.llm.suggest_job_titles",
        return_value=["HR Assistant", "HR Coordinator", "People Ops Assistant"],
    ):
        out = _handle_intake(db, session, "1 to 2 years, 3 to 5 years")
    assert session.intake_progress["step"] == "current_job_titles"
    assert session.intake_progress["role_name"] == "HR Assistant"
    assert "HR Assistant" in out["assistant_message"] or "Suggested" in out["assistant_message"]

    # titles confirm
    with patch("app.services.chat._save_msg"):
        out = _handle_intake(db, session, "confirm")
    assert session.intake_progress["step"] == "anchor_keyword"
    assert session.intake_progress["role_name"] == "HR Assistant"

    # anchor
    with patch("app.services.chat._save_msg"):
        out = _handle_intake(db, session, "onboarding")
    assert session.intake_progress["step"] == "pool_cap"

    # pool
    with patch("app.services.chat._save_msg"):
        out = _handle_intake(db, session, "25")
    assert session.intake_progress["step"] == "email_enrichment"

    # email
    with patch("app.services.chat._save_msg"):
        out = _handle_intake(db, session, "LinkedIn only")
    assert session.state == "confirm"
    assert session.intake_progress["role_name"] == "HR Assistant"
    assert "Role      : HR Assistant" in out["assistant_message"]
    assert "**" not in out["assistant_message"]


def test_role_name_not_overwritten_by_set_progress():
    session = _fake_session(step="function", role_name="HVAC Sales Engineer")
    _set_progress(session, role_name="Sales", function_key="sales")
    assert _progress(session)["role_name"] == "HVAC Sales Engineer"


if __name__ == "__main__":
    test_clean_answer_strips_bullets()
    test_role_name_not_overwritten_by_set_progress()
    test_intake_advances_and_keeps_role_name()
    print("ok: intake FSM advances with stable role_name")
