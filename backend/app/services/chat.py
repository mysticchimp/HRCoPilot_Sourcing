"""Chat state machine — intake → confirm → ready. No scoring."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.apify.client import compile_retrieval
from app.maps import (
    BROAD_FUNCTIONS,
    DEFAULT_LOCATION,
    YEARS_LABELS,
)
from app.models import ChatMessage, ChatSession, Role
from app.services import llm
from app.services.pull_batch import list_role_candidates, pull_batch
from app.services.validation import (
    check_titles_nationality,
    find_nationality_hit,
    resolve_function,
    resolve_years_tokens,
    validate_anchor,
)

# Intake step keys in fixed order (after role_name turn)
INTAKE_STEPS = [
    "location",  # usually skipped — default UAE
    "function",
    "years_of_experience",
    "current_job_titles",
    "anchor_keyword",
    "pool_cap",
    "email_enrichment",
]

ANCHOR_HINT = (
    "Give me 1-3 words that anchor the domain - not a sentence, just the "
    "core terms someone in this field would have on their profile.\n\n"
    "Good:  HVAC\n"
    "       ductwork manufacturing\n"
    "       fire rated duct\n\n"
    'Avoid: "experienced HVAC engineer with 5+ years"  (sentence - collapses results)\n'
    '       "HVAC OR ductwork OR MEP"                   (it\'s already AND, not OR)'
)

ANCHOR_BLANK_LINE = (
    "\n       leaving it blank on a broad function          (HR/Sales alone over\n"
    "                                                       the whole UAE returns\n"
    "                                                       thousands)"
)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return s or "role"


def _unique_slug(db: Session, base: str) -> str:
    slug = base
    n = 2
    while db.execute(select(Role.id).where(Role.slug == slug)).scalar_one_or_none():
        slug = f"{base}_{n}"
        n += 1
    return slug


def _save_msg(db: Session, session_id: uuid.UUID, role: str, content: str) -> ChatMessage:
    msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role=role,
        content=content,
    )
    db.add(msg)
    return msg


def _touch(session: ChatSession) -> None:
    session.updated_at = datetime.now(timezone.utc)


def _progress(session: ChatSession) -> dict:
    return dict(session.intake_progress or {})


def _set_progress(session: ChatSession, **kwargs: Any) -> dict:
    p = _progress(session)
    p.update(kwargs)
    session.intake_progress = p
    return p


def format_confirm_summary(p: dict) -> str:
    years = p.get("years_of_experience") or []
    titles = p.get("current_job_titles") or []
    contact = (
        "Includes email"
        if p.get("profileScraperMode") == "Full + email search"
        else "LinkedIn only"
    )
    anchor = p.get("searchQuery") or "(none)"
    return (
        f"Role      : {p.get('role_name', '')}\n"
        f"Location  : {p.get('location', DEFAULT_LOCATION)}\n"
        f"Function  : {p.get('function_label', p.get('functions', [''])[0] if p.get('functions') else '')}\n"
        f"Years     : {', '.join(years) if isinstance(years, list) else years}\n"
        f"Titles    : {', '.join(titles) if isinstance(titles, list) else titles}\n"
        f"Anchor    : {anchor}\n"
        f"Pool cap  : {p.get('pool_cap', 25)} per batch\n"
        f"Contact   : {contact}\n\n"
        "Confirm and pull the first batch? (yes / change something)"
    )


def _function_prompt(role_name: str) -> str:
    opts = llm.plausible_functions_for_role(role_name)
    lines = "\n".join(f"  · {o}" for o in opts)
    return (
        f"Which LinkedIn function best fits **{role_name}**?\n"
        f"Pick exactly one from this list:\n{lines}\n\n"
        "(Answer must match a label above — case doesn't matter.)"
    )


def _years_prompt() -> str:
    lines = "\n".join(f"  · {y}" for y in YEARS_LABELS)
    return (
        "Years of experience — pick one or more (comma-separated):\n"
        f"{lines}\n\n"
        "Example: 3 to 5 years, 6 to 10 years"
    )


def _titles_prompt(titles: list[str]) -> str:
    numbered = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
    return (
        "Suggested current job titles for the search:\n"
        f"{numbered}\n\n"
        "Reply with add / remove / confirm in plain language "
        "(e.g. \"drop 3, add Talent Coordinator, looks good\")."
    )


def _anchor_prompt(function_key: str) -> str:
    hint = ANCHOR_HINT
    if function_key in BROAD_FUNCTIONS:
        hint = ANCHOR_HINT + ANCHOR_BLANK_LINE
    return hint


def _email_prompt() -> str:
    return (
        "Do you need direct email addresses for outreach, or is LinkedIn enough?\n"
        "(LinkedIn only / yes, include emails)"
    )


def _pool_prompt() -> str:
    return "How many profiles should I pull this batch - 25, 50, more?"


def start_session(db: Session, role_slug: str | None = None) -> dict:
    """Create a chat session. If role_slug given and exists, resume/ready; else intake."""
    role = None
    if role_slug:
        role = db.execute(select(Role).where(Role.slug == role_slug)).scalar_one_or_none()

    if role and role.retrieval and role.retrieval.get("functions"):
        # Existing configured role → ready session
        session = ChatSession(
            id=uuid.uuid4(),
            role_id=role.id,
            state="ready",
            intake_progress={
                "role_name": role.role_name,
                "slug": role.slug,
                "step": "done",
            },
        )
        db.add(session)
        greeting = (
            f"Ready on **{role.role_name}**. "
            "Ask me to pull another batch, show the table, or change a filter."
        )
        _save_msg(db, session.id, "assistant", greeting)
        db.commit()
        return _session_payload(db, session, greeting)

    # Fresh intake
    session = ChatSession(
        id=uuid.uuid4(),
        role_id=role.id if role else None,
        state="intake",
        intake_progress={"step": "role_name"},
    )
    db.add(session)
    greeting = (
        "What's the role name? "
        f"(Location defaults to {DEFAULT_LOCATION} — say otherwise if you need a different market.)"
    )
    _save_msg(db, session.id, "assistant", greeting)
    db.commit()
    return _session_payload(db, session, greeting)


def _session_payload(
    db: Session,
    session: ChatSession,
    assistant_text: str,
    *,
    candidates: list | None = None,
    summary: str | None = None,
    action: str | None = None,
) -> dict:
    role = db.get(Role, session.role_id) if session.role_id else None
    return {
        "session_id": str(session.id),
        "state": session.state,
        "role_slug": role.slug if role else (_progress(session).get("slug")),
        "intake_progress": _progress(session),
        "assistant_message": assistant_text,
        "candidates": candidates,
        "summary": summary,
        "action": action,
    }


def handle_message(db: Session, role_slug: str, message: str) -> dict:
    """POST /chat/{role_slug}/message entry — route by session state."""
    message = (message or "").strip()
    if not message:
        raise ValueError("empty message")

    # Prefer latest session for this slug; if slug is "new", start intake
    role = None
    if role_slug and role_slug != "new":
        role = db.execute(select(Role).where(Role.slug == role_slug)).scalar_one_or_none()

    session = None
    if role:
        session = db.execute(
            select(ChatSession)
            .where(ChatSession.role_id == role.id)
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    # Also allow addressing a brand-new intake session keyed by slug "new"
    if session is None and role_slug == "new":
        session = db.execute(
            select(ChatSession)
            .where(ChatSession.role_id.is_(None), ChatSession.state == "intake")
            .order_by(ChatSession.updated_at.desc())
            .limit(1)
        ).scalar_one_or_none()

    if session is None:
        # Auto-start
        started = start_session(db, None if role_slug == "new" else role_slug)
        session = db.get(ChatSession, uuid.UUID(started["session_id"]))

    assert session is not None
    _save_msg(db, session.id, "user", message)
    _touch(session)

    if session.state == "intake":
        result = _handle_intake(db, session, message)
    elif session.state == "confirm":
        result = _handle_confirm(db, session, message)
    elif session.state == "ready":
        result = _handle_ready(db, session, message)
    else:
        result = _session_payload(db, session, "Session in unknown state — start a new role.")

    db.commit()
    return result


# ── INTAKE ──────────────────────────────────────────────────────────────────


def _handle_intake(db: Session, session: ChatSession, message: str) -> dict:
    p = _progress(session)
    step = p.get("step", "role_name")

    if step == "role_name":
        return _intake_role_name(db, session, message)
    if step == "location":
        return _intake_location(db, session, message)
    if step == "function":
        return _intake_function(db, session, message)
    if step == "years_of_experience":
        return _intake_years(db, session, message)
    if step == "current_job_titles":
        return _intake_titles(db, session, message)
    if step == "anchor_keyword":
        return _intake_anchor(db, session, message)
    if step == "pool_cap":
        return _intake_pool(db, session, message)
    if step == "email_enrichment":
        return _intake_email(db, session, message)
    # Re-open single field from confirm
    if step.startswith("edit_"):
        return _intake_edit_field(db, session, message, step[len("edit_"):])

    reply = "I'm not sure where we left off — what's the role name?"
    _set_progress(session, step="role_name")
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_role_name(db: Session, session: ChatSession, message: str) -> dict:
    # Detect location override on this turn
    loc = DEFAULT_LOCATION
    ask_location = False
    lower = message.lower()
    loc_match = re.search(
        r"\b(?:in|for|location[:\s]+)([A-Za-z][A-Za-z\s]+)$",
        message,
        re.IGNORECASE,
    )
    # Simple: if user mentions a place that isn't UAE
    if any(
        x in lower
        for x in (
            "saudi",
            "dubai",
            "abu dhabi",
            "qatar",
            "kuwait",
            "bahrain",
            "oman",
            "egypt",
            "uk",
            "united kingdom",
            "india",
            "singapore",
        )
    ) or ("location" in lower and "uae" not in lower and "emirates" not in lower):
        # Keep full message as role if unclear — try to split
        ask_location = True

    role_name = message
    # Strip trailing location phrases for cleaner role name
    role_name = re.sub(
        r"\s+(?:in|based in|location[:\s]+).*$",
        "",
        role_name,
        flags=re.IGNORECASE,
    ).strip() or message.strip()

    if loc_match and ask_location:
        loc = loc_match.group(1).strip()

    slug = _unique_slug(db, _slugify(role_name))
    _set_progress(
        session,
        step="location" if ask_location and loc == DEFAULT_LOCATION else "function",
        role_name=role_name,
        slug=slug,
        location=loc if not ask_location else None,
        pending_location_ask=ask_location,
    )

    if ask_location and not loc_match:
        _set_progress(session, step="location")
        reply = f"Got it — **{role_name}**. Which location should I search? (default: {DEFAULT_LOCATION})"
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    _set_progress(session, location=loc, step="function")
    reply = _function_prompt(role_name)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_location(db: Session, session: ChatSession, message: str) -> dict:
    loc = message.strip() or DEFAULT_LOCATION
    p = _set_progress(session, location=loc, step="function")
    reply = _function_prompt(p["role_name"])
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_function(db: Session, session: ChatSession, message: str) -> dict:
    p = _progress(session)
    key = resolve_function(message)
    if key is None:
        # Also try matching against the shown plausible subset only — still must be in map
        reply = (
            f"I need an exact match from the list.\n\n"
            + _function_prompt(p.get("role_name", "this role"))
        )
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    label = key.title() if key != "human resources" else "Human Resources"
    _set_progress(
        session,
        functions=[label],
        function_key=key,
        function_label=label,
        step="years_of_experience",
    )
    reply = _years_prompt()
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_years(db: Session, session: ChatSession, message: str) -> dict:
    valid, invalid = resolve_years_tokens(message)
    if valid is None:
        reply = (
            f"I didn't recognize: {', '.join(invalid)}. "
            "Please use exact labels from the list.\n\n" + _years_prompt()
        )
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    p = _set_progress(session, years_of_experience=valid, step="current_job_titles")
    titles = llm.suggest_job_titles(p["role_name"], valid)
    nat = check_titles_nationality(titles)
    if nat:
        # Extremely unlikely from generator; still enforce
        titles = [t for t in titles if not find_nationality_hit(t)]
    _set_progress(session, suggested_titles=titles, current_job_titles=titles)
    reply = _titles_prompt(titles)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_titles(db: Session, session: ChatSession, message: str) -> dict:
    p = _progress(session)
    shown = list(p.get("suggested_titles") or p.get("current_job_titles") or [])
    lower = message.lower().strip()
    if lower in ("confirm", "looks good", "ok", "okay", "yes", "good", "lgtm"):
        titles = shown
    else:
        titles = llm.apply_title_edits(shown, message)

    nat = check_titles_nationality(titles)
    if nat:
        _save_msg(db, session.id, "assistant", nat)
        return _session_payload(db, session, nat)

    if not titles:
        reply = "I need at least one title. " + _titles_prompt(shown)
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    # If user said something that wasn't pure confirm, show updated list once for confirm
    if lower not in ("confirm", "looks good", "ok", "okay", "yes", "good", "lgtm"):
        if titles != shown:
            _set_progress(session, suggested_titles=titles, current_job_titles=titles)
            reply = (
                "Updated titles:\n"
                + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles))
                + '\n\nSay "confirm" to continue, or keep editing.'
            )
            _save_msg(db, session.id, "assistant", reply)
            return _session_payload(db, session, reply)

    fk = p.get("function_key") or (
        (p.get("functions") or [""])[0].lower() if p.get("functions") else ""
    )
    _set_progress(session, current_job_titles=titles, step="anchor_keyword")
    reply = _anchor_prompt(fk)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_anchor(db: Session, session: ChatSession, message: str) -> dict:
    p = _progress(session)
    fk = p.get("function_key") or ""
    broad = fk in BROAD_FUNCTIONS
    # Also check titles for nationality
    nat = check_titles_nationality(p.get("current_job_titles") or [])
    if nat:
        _save_msg(db, session.id, "assistant", nat)
        return _session_payload(db, session, nat)

    err = validate_anchor(message, broad_function=broad)
    if err:
        _save_msg(db, session.id, "assistant", err)
        return _session_payload(db, session, err)

    # Accept literal (no truncate) — empty allowed only for narrow functions
    anchor = message.strip()
    # Reject if titles+anchor together hit nationality (anchor already checked)
    _set_progress(session, searchQuery=anchor, step="pool_cap")
    reply = _pool_prompt()
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_pool(db: Session, session: ChatSession, message: str) -> dict:
    n = llm.extract_pool_cap(message)
    if n is None or n < 10 or n > 150:
        reply = "Please give an integer between 10 and 150. " + _pool_prompt()
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)
    _set_progress(session, pool_cap=n, step="email_enrichment")
    reply = _email_prompt()
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_email(db: Session, session: ChatSession, message: str) -> dict:
    lower = message.lower()
    if any(x in lower for x in ("email", "emails", "yes", "outreach", "direct")):
        mode = "Full + email search"
    elif any(x in lower for x in ("linkedin", "enough", "no", "only")):
        mode = "Full"
    else:
        reply = (
            "Please choose: **LinkedIn only** or **yes, include emails**. "
            "(Short mode is never offered — we always enrich profiles we keep.)"
        )
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    p = _set_progress(session, profileScraperMode=mode, step="done")
    session.state = "confirm"
    reply = format_confirm_summary(p)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _intake_edit_field(db: Session, session: ChatSession, message: str, field: str) -> dict:
    """Re-answer a single field after confirm 'change something'."""
    # Temporarily route into the matching intake handler then re-show confirm
    mapping = {
        "location": "location",
        "function": "function",
        "years": "years_of_experience",
        "years_of_experience": "years_of_experience",
        "titles": "current_job_titles",
        "current_job_titles": "current_job_titles",
        "anchor": "anchor_keyword",
        "anchor_keyword": "anchor_keyword",
        "pool": "pool_cap",
        "pool_cap": "pool_cap",
        "email": "email_enrichment",
        "email_enrichment": "email_enrichment",
        "contact": "email_enrichment",
    }
    step = mapping.get(field, field)
    _set_progress(session, step=step, _return_to_confirm=True)

    # Process as if on that step
    handlers = {
        "location": _intake_location,
        "function": _intake_function,
        "years_of_experience": _intake_years,
        "current_job_titles": _intake_titles,
        "anchor_keyword": _intake_anchor,
        "pool_cap": _intake_pool,
        "email_enrichment": _intake_email,
    }
    handler = handlers.get(step)
    if not handler:
        session.state = "confirm"
        reply = format_confirm_summary(_progress(session))
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    result = handler(db, session, message)
    p = _progress(session)
    if p.get("_return_to_confirm") and session.state != "confirm":
        # If handler advanced past the field without going to confirm, snap back
        # only when the field itself was accepted (step changed away from edit target)
        if p.get("step") != step:
            session.state = "confirm"
            _set_progress(session, step="done", _return_to_confirm=False)
            reply = format_confirm_summary(_progress(session))
            _save_msg(db, session.id, "assistant", reply)
            return _session_payload(db, session, reply)
    return result


# ── CONFIRM ─────────────────────────────────────────────────────────────────


def _handle_confirm(db: Session, session: ChatSession, message: str) -> dict:
    lower = message.lower().strip()
    p = _progress(session)

    if p.get("awaiting_change_field"):
        field = message.strip().lower().replace(" ", "_")
        aliases = {
            "years": "years_of_experience",
            "titles": "current_job_titles",
            "anchor": "anchor_keyword",
            "pool": "pool_cap",
            "contact": "email_enrichment",
            "email": "email_enrichment",
            "function": "function",
            "location": "location",
            "role": "role_name",
        }
        field = aliases.get(field, field)
        _set_progress(session, awaiting_change_field=False, step=f"edit_{field}")
        session.state = "intake"
        prompts = {
            "location": f"New location? (current: {p.get('location', DEFAULT_LOCATION)})",
            "function": _function_prompt(p.get("role_name", "")),
            "years_of_experience": _years_prompt(),
            "current_job_titles": _titles_prompt(p.get("current_job_titles") or []),
            "anchor_keyword": _anchor_prompt(p.get("function_key") or ""),
            "pool_cap": _pool_prompt(),
            "email_enrichment": _email_prompt(),
        }
        reply = prompts.get(field, "Which field? (location / function / years / titles / anchor / pool / contact)")
        if field not in prompts:
            session.state = "confirm"
            _set_progress(session, awaiting_change_field=True, step="done")
            reply = "Which field do you want to change? (location / function / years / titles / anchor / pool / contact)"
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    if any(x in lower for x in ("change", "edit", "modify", "adjust")):
        _set_progress(session, awaiting_change_field=True)
        reply = "Which field? (location / function / years / titles / anchor / pool / contact)"
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    if lower.startswith("y") or lower in ("confirm", "pull", "go", "ok", "okay", "lgtm"):
        return _finalize_and_pull(db, session)

    reply = format_confirm_summary(p)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply)


def _build_retrieval(p: dict) -> dict:
    return {
        "functions": p.get("functions") or [],
        "seniority": [],
        "location": p.get("location") or DEFAULT_LOCATION,
        "yearsOfExperience": p.get("years_of_experience") or [],
        "currentJobTitles": p.get("current_job_titles") or [],
        "searchQuery": p.get("searchQuery") or "",
        "industryIds": [],
        "profileScraperMode": p.get("profileScraperMode") or "Full",
        "pool_cap": int(p.get("pool_cap") or 25),
    }


def _finalize_and_pull(db: Session, session: ChatSession) -> dict:
    p = _progress(session)
    retrieval = _build_retrieval(p)
    # Validate compile works before writing
    compile_retrieval({"retrieval": retrieval}, retrieval["pool_cap"])

    slug = p.get("slug") or _unique_slug(db, _slugify(p["role_name"]))
    role = None
    if session.role_id:
        role = db.get(Role, session.role_id)
    if role is None:
        role = db.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()

    if role is None:
        role = Role(
            id=uuid.uuid4(),
            slug=slug,
            role_name=p["role_name"],
            client=p.get("client"),
            retrieval=retrieval,
            last_page=0,
        )
        db.add(role)
        db.flush()
    else:
        role.retrieval = retrieval
        role.role_name = p["role_name"]
        role.last_page = 0
        role.updated_at = datetime.now(timezone.utc)

    session.role_id = role.id
    session.state = "ready"
    _set_progress(session, slug=role.slug, step="done")
    db.flush()

    batch_size = int(retrieval.get("pool_cap") or 25)
    result = pull_batch(db, role.id, batch_size=batch_size)
    summary = result.get("summary", "")
    reply = f"Filters locked. {summary}"
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(
        db,
        session,
        reply,
        candidates=result.get("candidates"),
        summary=summary,
        action="PULL_BATCH",
    )


# ── READY ───────────────────────────────────────────────────────────────────


def _handle_ready(db: Session, session: ChatSession, message: str) -> dict:
    p = _progress(session)
    role = db.get(Role, session.role_id) if session.role_id else None
    if not role:
        reply = "No role attached — start a new role."
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    # Sub-flows: awaiting same/change after PULL_BATCH ask; awaiting change confirm
    if p.get("awaiting_pull_choice"):
        return _ready_pull_choice(db, session, role, message)
    if p.get("awaiting_change_confirm"):
        return _ready_change_confirm(db, session, role, message)
    if p.get("awaiting_change_spec"):
        return _ready_change_spec(db, session, role, message)

    intent = llm.classify_ready_intent(message)

    if intent == "PULL_BATCH":
        _set_progress(session, awaiting_pull_choice=True)
        reply = "Same filters as before, or change something first?"
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply, action="ASK_PULL")

    if intent == "SHOW_TABLE":
        rows = list_role_candidates(db, role.id)
        reply = f"Showing {len(rows)} candidates for **{role.role_name}** (most recent first)."
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(
            db, session, reply, candidates=rows, action="SHOW_TABLE"
        )

    reply = llm.conversational_reply(message, role.role_name)
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(db, session, reply, action="OTHER")


def _ready_pull_choice(db: Session, session: ChatSession, role: Role, message: str) -> dict:
    lower = message.lower().strip()
    _set_progress(session, awaiting_pull_choice=False)

    if any(x in lower for x in ("change", "edit", "modify", "different", "update")):
        _set_progress(session, awaiting_change_spec=True)
        reply = "What do you want to change, and to what?"
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    # default / "same"
    batch_size = int((role.retrieval or {}).get("pool_cap") or 25)
    result = pull_batch(db, role.id, batch_size=batch_size)
    summary = result.get("summary", "")
    reply = summary
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(
        db,
        session,
        reply,
        candidates=result.get("candidates"),
        summary=summary,
        action="PULL_BATCH",
    )


def _ready_change_spec(db: Session, session: ChatSession, role: Role, message: str) -> dict:
    current = dict(role.retrieval or {})
    parsed = llm.parse_change_field(message, current)
    _set_progress(session, awaiting_change_spec=False)
    if not parsed:
        reply = "I couldn't tell which field to change — try e.g. \"change years to 6-10 years\"."
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    confirm_line = parsed.get("confirm_line") or (
        f"Change {parsed['field']} to {parsed['value']}? Applies to your next pull."
    )
    _set_progress(
        session,
        awaiting_change_confirm=True,
        pending_change=parsed,
    )
    _save_msg(db, session.id, "assistant", confirm_line)
    return _session_payload(db, session, confirm_line)


def _ready_change_confirm(db: Session, session: ChatSession, role: Role, message: str) -> dict:
    lower = message.lower().strip()
    pending = (_progress(session).get("pending_change") or {})
    _set_progress(session, awaiting_change_confirm=False, pending_change=None)

    if not (lower.startswith("y") or lower in ("ok", "okay", "confirm", "apply")):
        reply = "Cancelled — filters unchanged. Say when you want to pull a batch."
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    retrieval = dict(role.retrieval or {})
    field = pending.get("field")
    value = pending.get("value")

    if field == "location":
        retrieval["location"] = str(value)
    elif field == "function":
        key = resolve_function(str(value))
        if not key:
            reply = f"Invalid function {value!r} — change cancelled."
            _save_msg(db, session.id, "assistant", reply)
            return _session_payload(db, session, reply)
        label = "Human Resources" if key == "human resources" else key.title()
        retrieval["functions"] = [label]
    elif field == "years_of_experience":
        if isinstance(value, list):
            valid, invalid = resolve_years_tokens(", ".join(str(v) for v in value))
        else:
            valid, invalid = resolve_years_tokens(str(value))
        if valid is None:
            reply = f"Invalid years {invalid} — change cancelled."
            _save_msg(db, session.id, "assistant", reply)
            return _session_payload(db, session, reply)
        retrieval["yearsOfExperience"] = valid
    elif field == "current_job_titles":
        titles = value if isinstance(value, list) else [str(value)]
        nat = check_titles_nationality(titles)
        if nat:
            _save_msg(db, session.id, "assistant", nat)
            return _session_payload(db, session, nat)
        retrieval["currentJobTitles"] = titles
    elif field == "anchor_keyword":
        fk = (retrieval.get("functions") or [""])[0].lower()
        err = validate_anchor(str(value), broad_function=fk in BROAD_FUNCTIONS)
        if err:
            _save_msg(db, session.id, "assistant", err)
            return _session_payload(db, session, err)
        retrieval["searchQuery"] = str(value).strip()
    elif field == "pool_cap":
        n = int(value)
        if n < 10 or n > 150:
            reply = "Pool cap must be 10–150 — change cancelled."
            _save_msg(db, session.id, "assistant", reply)
            return _session_payload(db, session, reply)
        retrieval["pool_cap"] = n
    elif field == "email_enrichment":
        retrieval["profileScraperMode"] = (
            "Full + email search" if value in (True, "true", "yes", 1) else "Full"
        )
    else:
        reply = f"Unknown field {field} — change cancelled."
        _save_msg(db, session.id, "assistant", reply)
        return _session_payload(db, session, reply)

    # Changed filter = different search → pagination restarts
    role.retrieval = retrieval
    role.last_page = 0
    role.updated_at = datetime.now(timezone.utc)
    db.flush()

    batch_size = int(retrieval.get("pool_cap") or 25)
    result = pull_batch(db, role.id, batch_size=batch_size)
    summary = result.get("summary", "")
    reply = f"Filter updated; pagination reset. {summary}"
    _save_msg(db, session.id, "assistant", reply)
    return _session_payload(
        db,
        session,
        reply,
        candidates=result.get("candidates"),
        summary=summary,
        action="PULL_BATCH",
    )
