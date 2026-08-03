"""Validation helpers for intake — closed-set matching + nationality blocklist."""

from __future__ import annotations

import re

from app.maps import FUNCTION_MAP, YEARS_MAP

# UAE Labour Law: never accept nationality / demonym filters in search.
NATIONALITY_BLOCKLIST = {
    "filipino", "filipina", "philippines", "pinoy", "pinay",
    "indian", "india", "emirati", "emirati's", "uae national",
    "pakistani", "pakistan", "egyptian", "egypt",
    "british", "american", "canadian", "australian",
    "chinese", "chinese-speaking",  # nationality form — language handled separately
    "korean", "japanese", "thai", "vietnamese", "indonesian",
    "bangladeshi", "nepal", "nepalese", "nepali", "sri lankan",
    "lebanese", "syrian", "jordanian", "palestinian", "iraqi",
    "iranian", "saudi", "saudi arabian", "kuwaiti", "bahraini", "omani",
    "qatari", "sudanese", "moroccan", "tunisian", "algerian",
    "nigerian", "kenyan", "south african", "ghanaian",
    "russian", "ukrainian", "european", "western", "asian", "arab",
    "expat", "expatriate", "local hire", "national only",
}

SENTENCE_MARKERS = re.compile(
    r"\b(with|years|experience)\b|\s+or\s+",
    re.IGNORECASE,
)

# Strip markdown list markers users copy-paste from option lists (·, -, *, 1.)
_LIST_PREFIX = re.compile(
    r"^(?:[\s\u00b7\u2022\-\*]+|\d+[\.\)]\s*)+",
)


def clean_answer(raw: str) -> str:
    """Strip list/bullet prefixes and surrounding whitespace from a reply token."""
    s = (raw or "").strip()
    s = _LIST_PREFIX.sub("", s).strip()
    # Also strip accidental wrapping quotes
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def resolve_function(raw: str) -> str | None:
    """Exact closed-set match (case-insensitive) against FUNCTION_MAP keys."""
    key = clean_answer(raw).lower()
    return key if key in FUNCTION_MAP else None


def resolve_years_tokens(raw: str) -> tuple[list[str] | None, list[str]]:
    """Parse comma-separated YoE tokens. Returns (canonical_labels, invalid_tokens)."""
    tokens = [clean_answer(t) for t in raw.split(",") if clean_answer(t)]
    if not tokens:
        return None, ["(empty)"]
    valid: list[str] = []
    invalid: list[str] = []
    # Build reverse lookup of lower -> canonical display
    canon = {k: k.title().replace("To", "to").replace("Than", "than") for k in YEARS_MAP}
    # Prefer the exact YEARS_LABELS casing
    from app.maps import YEARS_LABELS

    label_by_key = {lab.lower(): lab for lab in YEARS_LABELS}
    for t in tokens:
        k = t.lower()
        if k in YEARS_MAP:
            valid.append(label_by_key.get(k, canon[k]))
        else:
            invalid.append(t)
    if invalid:
        return None, invalid
    return valid, []

def find_nationality_hit(text: str) -> str | None:
    """Return the matched nationality/demonym token, or None."""
    lowered = text.lower()
    # Longest match first to catch multi-word phrases
    for term in sorted(NATIONALITY_BLOCKLIST, key=len, reverse=True):
        if term in lowered:
            return term
    return None


def validate_anchor(raw: str, *, broad_function: bool) -> str | None:
    """Return an error message if invalid, else None.

    Does not coerce or truncate — caller must re-ask.
    """
    text = raw.strip()
    if not text:
        if broad_function:
            return (
                "Don't leave the anchor blank on a broad function — "
                "give me 1-3 core domain words."
            )
        return None  # narrow functions may leave blank

    words = text.split()
    if len(words) > 3 or SENTENCE_MARKERS.search(text):
        return (
            "that reads like a sentence - can you narrow it to the 1-3 core words?"
        )

    hit = find_nationality_hit(text)
    if hit:
        return (
            "I can't filter or search by nationality - that's prohibited under "
            "UAE Labour Law. If you need a language requirement, we can add "
            f"[{hit} language] as a scoring signal instead - want me to note that "
            "for the ML model?"
        )
    return None


def check_titles_nationality(titles: list[str]) -> str | None:
    for t in titles:
        hit = find_nationality_hit(t)
        if hit:
            return (
                "I can't filter or search by nationality - that's prohibited under "
                "UAE Labour Law. If you need a language requirement, we can add "
                f"[{hit} language] as a scoring signal instead - want me to note that "
                "for the ML model?"
            )
    return None
