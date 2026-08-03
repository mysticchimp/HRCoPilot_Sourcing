"""Haiku helpers — title suggestions, title edits, ready-state intent."""

from __future__ import annotations

import json
import os
import re

from anthropic import Anthropic

from app.config import get_settings
from app.maps import FUNCTION_MAP

_client: Anthropic | None = None


def _anthropic() -> Anthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY") or get_settings().anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = Anthropic(api_key=key)
    return _client


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    return t.strip()


def suggest_job_titles(role_name: str, years: list[str] | None = None) -> list[str]:
    """Generate 4-8 title variants — reuses jd_to_spec rule #1 semantics."""
    years_note = ", ".join(years) if years else "not specified"
    system = (
        "You suggest LinkedIn current-job-title search variants for recruiting. "
        "ALWAYS populate 4-8 real title variants that match the LEVEL of this role "
        "(e.g. an assistant role -> Assistant, Coordinator, Administrator, Officer, "
        "Executive; a manager role -> Manager, Lead, Head). Titles are the primary "
        "level gate and must never be left empty. Return ONLY a JSON array of strings."
    )
    user = (
        f"Role name: {role_name}\n"
        f"Years-of-experience buckets (level cue): {years_note}\n"
        "Return 4-8 title variants."
    )
    resp = _anthropic().messages.create(
        model=get_settings().query_model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    data = json.loads(_strip_fences(resp.content[0].text))
    if not isinstance(data, list):
        raise ValueError("title suggestion did not return a list")
    titles = [str(t).strip() for t in data if str(t).strip()]
    return titles[:8]


def apply_title_edits(shown: list[str], user_reply: str) -> list[str]:
    """Map a plain-language add/remove/confirm reply onto the shown list.

    Never freely invent titles not already visible or explicitly typed by the user.
    """
    system = (
        "You update a numbered list of job titles based on the user's reply. "
        "You may only keep titles from the shown list, or add titles the user "
        "explicitly typed in their reply. Never invent new titles. "
        'Return ONLY JSON: {"titles": ["..."], "confirmed": true|false}.'
    )
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(shown))
    user = f"Shown titles:\n{numbered}\n\nUser reply:\n{user_reply}"
    resp = _anthropic().messages.create(
        model=get_settings().query_model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    data = json.loads(_strip_fences(resp.content[0].text))
    titles = [str(t).strip() for t in data.get("titles", []) if str(t).strip()]
    # Safety: only allow shown titles or substrings the user typed
    allowed = {t.lower(): t for t in shown}
    user_lower = user_reply.lower()
    out: list[str] = []
    for t in titles:
        if t.lower() in allowed:
            out.append(allowed[t.lower()])
        elif t.lower() in user_lower or t in user_reply:
            out.append(t)
        # else drop invented titles
    return out or list(shown)


def classify_ready_intent(message: str) -> str:
    """Classify a ready-state message into PULL_BATCH | SHOW_TABLE | OTHER."""
    system = (
        "Classify the recruiter's message into exactly one label:\n"
        "PULL_BATCH — they want to pull/fetch/scrape another batch of profiles\n"
        "SHOW_TABLE — they want to see/list/show current candidates or results\n"
        "OTHER — anything else (questions, chitchat)\n"
        'Return ONLY JSON: {"intent": "PULL_BATCH"|"SHOW_TABLE"|"OTHER"}.'
    )
    resp = _anthropic().messages.create(
        model=get_settings().query_model,
        max_tokens=80,
        system=system,
        messages=[{"role": "user", "content": message}],
    )
    data = json.loads(_strip_fences(resp.content[0].text))
    intent = str(data.get("intent", "OTHER")).upper()
    if intent not in ("PULL_BATCH", "SHOW_TABLE", "OTHER"):
        return "OTHER"
    return intent


def parse_change_field(message: str, current: dict) -> dict | None:
    """Identify which retrieval field to change and the new value.

    Returns {field, value, confirm_line} or None if unclear.
    """
    system = (
        "The user wants to change a sourcing filter. Current filters:\n"
        f"{json.dumps(current, ensure_ascii=False)}\n"
        "Fields: location, function, years_of_experience, current_job_titles, "
        "anchor_keyword, pool_cap, email_enrichment.\n"
        "Extract the field and new value. For function, value must be one of: "
        + ", ".join(sorted(FUNCTION_MAP.keys()))
        + ".\n"
        "For years_of_experience return an array of bucket labels. "
        "For email_enrichment return true (wants emails) or false (LinkedIn only). "
        "For pool_cap return an integer 10-150.\n"
        'Return ONLY JSON: {"field": "...", "value": ..., '
        '"confirm_line": "Change ... to ...? Applies to your next pull."} '
        'or {"field": null} if unclear.'
    )
    resp = _anthropic().messages.create(
        model=get_settings().query_model,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": message}],
    )
    data = json.loads(_strip_fences(resp.content[0].text))
    if not data.get("field"):
        return None
    return data


def conversational_reply(message: str, role_name: str) -> str:
    system = (
        f"You are Contra6's sourcing assistant for the role '{role_name}'. "
        "Answer briefly and helpfully. You do not score candidates — you only "
        "help with intake and pulling LinkedIn profiles. Do not invent scrape results."
    )
    resp = _anthropic().messages.create(
        model=get_settings().query_model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": message}],
    )
    return resp.content[0].text.strip()


def extract_pool_cap(message: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", message)
    if not m:
        return None
    return int(m.group(1))


def plausible_functions_for_role(role_name: str) -> list[str]:
    """Heuristic filter of FUNCTION_MAP labels plausible for a role name."""
    rn = role_name.lower()
    keywords = {
        "human resources": ["hr", "human resource", "people", "talent", "recruit"],
        "engineering": ["engineer", "engineering", "hvac", "mep", "mechanical", "electrical", "civil"],
        "sales": ["sales", "account executive", "business development"],
        "business development": ["business development", "bd ", "partnership"],
        "finance": ["finance", "accountant", "accounting", "controller", "cfo"],
        "accounting": ["accountant", "accounting", "bookkeep"],
        "marketing": ["marketing", "brand", "growth"],
        "operations": ["operations", "ops ", "plant", "factory"],
        "administrative": ["admin", "assistant", "coordinator", "office"],
        "information technology": ["it ", "software", "developer", "devops", "sysadmin"],
        "quality assurance": ["quality", "qa ", "qc "],
        "purchasing": ["purchas", "procurement", "buyer", "sourcing"],
        "program and project management": ["project manager", "program manager", "pmo"],
        "product management": ["product manager", "product owner"],
        "research": ["research", "r&d", "scientist"],
        "legal": ["legal", "lawyer", "counsel"],
        "healthcare services": ["nurse", "doctor", "clinic", "medical"],
        "real estate": ["real estate", "property"],
        "support": ["support", "helpdesk", "customer success"],
        "consulting": ["consultant", "consulting"],
        "education": ["teacher", "trainer", "education"],
    }
    hits: list[str] = []
    for fn, kws in keywords.items():
        if any(k in rn for k in kws):
            hits.append(fn.title() if fn != "human resources" else "Human Resources")
    if not hits:
        # Fall back to a useful mid-size subset rather than dumping all 26
        hits = [
            "Human Resources",
            "Engineering",
            "Operations",
            "Sales",
            "Finance",
            "Administrative",
            "Information Technology",
            "Program And Project Management",
        ]
    # Deduplicate preserving order; ensure keys resolve in FUNCTION_MAP
    out: list[str] = []
    seen: set[str] = set()
    for h in hits:
        key = h.lower()
        if key in FUNCTION_MAP and key not in seen:
            seen.add(key)
            out.append(h)
    return out
