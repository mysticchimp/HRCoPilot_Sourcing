"""Derive career-history fields from candidates.raw_profile.experience / about."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

# Local DB samples use startDate/endDate {text: "Mar 2026"|"Present"}.
# Full Apify payloads may also use startedOn/endedOn {year, month}.
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_DURATION_RE = re.compile(
    r"(?:(\d+)\s*yrs?)?(?:\s*(\d+)\s*mos?)?",
    re.IGNORECASE,
)


def _clamp_month(m: int) -> int:
    return max(1, min(12, int(m)))


def _parse_year_month(value: Any) -> Optional[tuple[int, int]]:
    """Return (year, month) or None. Month defaults to 1 when only year known."""
    if value is None:
        return None

    if isinstance(value, dict):
        if value.get("year") is not None:
            try:
                year = int(value["year"])
            except (TypeError, ValueError):
                return None
            month = 1
            if value.get("month") is not None:
                try:
                    month = _clamp_month(int(value["month"]))
                except (TypeError, ValueError):
                    month = 1
            return year, month

        text = value.get("text")
        if isinstance(text, str):
            return _parse_date_text(text)
        return None

    if isinstance(value, str):
        return _parse_date_text(value)

    return None


def _parse_date_text(text: str) -> Optional[tuple[int, int]]:
    raw = (text or "").strip()
    if not raw or raw.casefold() == "present":
        return None

    # "2020-03" / "2020"
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?", raw)
    if m:
        year = int(m.group(1))
        month = _clamp_month(int(m.group(2))) if m.group(2) else 1
        return year, month

    # "Mar 2026" / "March 2026" / "2026"
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", raw)
    if m:
        mon = _MONTHS.get(m.group(1).casefold())
        if mon:
            return int(m.group(2)), mon
        return None

    m = re.fullmatch(r"(\d{4})", raw)
    if m:
        return int(m.group(1)), 1

    return None


def _is_present(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip().casefold() == "present":
            return True
        # Empty endedOn object / null year often means current role.
        if value.get("year") is None and text is None and not value.get("month"):
            return True
        return False
    if isinstance(value, str) and value.strip().casefold() == "present":
        return True
    return False


def _parse_duration_months(text: str) -> Optional[int]:
    raw = (text or "").strip()
    if not raw:
        return None
    m = _DURATION_RE.fullmatch(raw.replace("  ", " "))
    if not m:
        # Allow "3 yrs 6 mos" with extra words stripped loosely
        m = re.search(r"(?:(\d+)\s*yrs?)?(?:\s*(\d+)\s*mos?)?", raw, re.I)
        if not m or (not m.group(1) and not m.group(2)):
            return None
    years = int(m.group(1) or 0)
    months = int(m.group(2) or 0)
    total = years * 12 + months
    return total if total > 0 else None


def _to_months(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + (ym[1] - 1)


def _from_months(total: int) -> tuple[int, int]:
    year = total // 12
    month = (total % 12) + 1
    return year, month


def _company_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def _normalize_positions(experience: list) -> list[dict[str, Any]]:
    """Normalize raw experience entries into dated positions (oldest first)."""
    today = date.today()
    today_ym = (today.year, today.month)
    positions: list[dict[str, Any]] = []

    for raw in experience:
        if not isinstance(raw, dict):
            continue
        title = (raw.get("position") or raw.get("title") or "").strip()
        company = (raw.get("companyName") or raw.get("company") or "").strip()
        if isinstance(company, dict):
            company = (company.get("name") or "").strip()

        start_raw = raw.get("startedOn")
        if start_raw is None:
            start_raw = raw.get("startDate")
        end_raw = raw.get("endedOn")
        if end_raw is None:
            end_raw = raw.get("endDate")

        start = _parse_year_month(start_raw)
        present = _is_present(end_raw)
        end = None if present else _parse_year_month(end_raw)

        duration_months = _parse_duration_months(str(raw.get("duration") or ""))

        # Infer missing start from end + duration, or end from start + duration.
        if start is None and end is not None and duration_months:
            start = _from_months(_to_months(end) - duration_months)
        if start is None and present and duration_months:
            start = _from_months(_to_months(today_ym) - duration_months)
        if end is None and not present and start is not None and duration_months:
            end = _from_months(_to_months(start) + duration_months)

        if start is None:
            # Can't place on timeline without a start; keep duration-only for years sum.
            if duration_months:
                positions.append(
                    {
                        "title": title,
                        "company": company,
                        "start": None,
                        "end": None,
                        "present": present,
                        "duration_months": duration_months,
                        "start_label": None,
                    }
                )
            continue

        if present or end is None:
            end = today_ym
            present = True

        if _to_months(end) < _to_months(start):
            end = start

        positions.append(
            {
                "title": title,
                "company": company,
                "start": start,
                "end": end,
                "present": present,
                "duration_months": duration_months
                or max(1, _to_months(end) - _to_months(start)),
                "start_label": str(start[0]),
            }
        )

    dated = [p for p in positions if p["start"] is not None]
    dated.sort(key=lambda p: (_to_months(p["start"]), _to_months(p["end"])))
    undated = [p for p in positions if p["start"] is None]
    return dated + undated


def _merge_interval_months(intervals: list[tuple[int, int]]) -> int:
    """Sum months covered by possibly-overlapping [start, end) month indices."""
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    cur_s, cur_e = ordered[0]
    for s, e in ordered[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += max(0, cur_e - cur_s)
            cur_s, cur_e = s, e
    total += max(0, cur_e - cur_s)
    return total


def _job_changes(positions: list[dict[str, Any]]) -> int:
    """Count company transitions chronologically (not title changes in-company)."""
    dated = [p for p in positions if p.get("start") is not None]
    if not dated:
        # Fall back to distinct companies among undated entries.
        companies = {_company_key(p["company"]) for p in positions if p.get("company")}
        companies.discard("")
        return max(0, len(companies) - 1)

    changes = 0
    prev = _company_key(dated[0].get("company") or "")
    for p in dated[1:]:
        cur = _company_key(p.get("company") or "")
        if not cur:
            continue
        if prev and cur != prev:
            changes += 1
        if cur:
            prev = cur
    return changes


def _gaps(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gaps between consecutive roles by start date (ended -> next started)."""
    dated = [p for p in positions if p.get("start") is not None and p.get("end") is not None]
    if len(dated) < 2:
        return []

    gaps: list[dict[str, Any]] = []
    for prev, nxt in zip(dated, dated[1:]):
        # If overlapping / concurrent, no gap.
        gap_months = _to_months(nxt["start"]) - _to_months(prev["end"])
        if gap_months > 3:  # > ~3 months marked visible
            gaps.append(
                {
                    "after_company": prev.get("company") or "",
                    "before_company": nxt.get("company") or "",
                    "months": gap_months,
                    "start": prev["end"],
                    "end": nxt["start"],
                }
            )
    return gaps


def _timeline(positions: list[dict[str, Any]], gaps: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    dated = [p for p in positions if p.get("start") is not None]
    if not dated:
        return None

    start_year = dated[0]["start"][0]
    end_present = any(p.get("present") for p in dated)
    end_year = date.today().year if end_present else dated[-1]["end"][0]
    start_m = _to_months(dated[0]["start"])
    end_m = _to_months((end_year, 12) if not end_present else (date.today().year, date.today().month))
    span = max(1, end_m - start_m)

    markers = []
    for p in dated:
        pct = ((_to_months(p["start"]) - start_m) / span) * 100
        markers.append(
            {
                "pct": round(max(0, min(100, pct)), 2),
                "year": p["start"][0],
                "title": p.get("title") or "",
                "company": p.get("company") or "",
                "present": bool(p.get("present")),
            }
        )

    gap_segments = []
    for g in gaps:
        s = ((_to_months(g["start"]) - start_m) / span) * 100
        e = ((_to_months(g["end"]) - start_m) / span) * 100
        gap_segments.append(
            {
                "start_pct": round(max(0, min(100, s)), 2),
                "end_pct": round(max(0, min(100, e)), 2),
                "months": g["months"],
                "label": f"Gap ({g['months']}mo)",
            }
        )

    return {
        "start_year": start_year,
        "end_label": "Present" if end_present else str(end_year),
        "markers": markers,
        "gaps": gap_segments,
    }


def build_career_summary(raw_profile: Any) -> dict[str, Any]:
    """Compute review-card career fields from a raw Apify profile dict."""
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    about = (profile.get("about") or "").strip() or None
    experience = profile.get("experience") or []
    if not isinstance(experience, list):
        experience = []

    positions = _normalize_positions(experience)

    years_exp: Optional[float] = None
    intervals = [
        (_to_months(p["start"]), _to_months(p["end"]))
        for p in positions
        if p.get("start") is not None and p.get("end") is not None
    ]
    if intervals:
        months = _merge_interval_months(intervals)
        if months > 0:
            years_exp = round(months / 12, 1)
    else:
        # Duration-only fallback (no parseable dates).
        dur = sum(int(p["duration_months"]) for p in positions if p.get("duration_months"))
        if dur > 0:
            years_exp = round(dur / 12, 1)

    gaps = _gaps(positions)
    longest = max((g["months"] for g in gaps), default=0)
    # Show "None" when no meaningful gap (>1 month).
    longest_gap_months: Optional[int]
    if longest <= 1:
        longest_gap_months = None
    else:
        longest_gap_months = longest

    return {
        "about": about,
        "years_experience": years_exp,
        "job_changes": _job_changes(positions),
        "longest_gap_months": longest_gap_months,
        "timeline": _timeline(positions, gaps),
        "position_count": len(positions),
    }
