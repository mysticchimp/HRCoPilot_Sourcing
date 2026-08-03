"""Apify client — ported from contra6_source2.py (auth, compile, probe, fetch, compact)."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any

import requests

from app.maps import DEFAULT_LOCATION, FUNCTION_MAP, SENIORITY_MAP, YEARS_MAP

logger = logging.getLogger("sourcing.apify")

ACTOR = "harvestapi~linkedin-profile-search"

# Async start → poll → fetch. Per-request timeouts stay short; only the loop
# may run for minutes while Apify retries flaky LinkedIn pages.
POLL_EVERY_SECS = 4
POLL_REQUEST_TIMEOUT = 12
START_REQUEST_TIMEOUT = 15
DATASET_REQUEST_TIMEOUT = 90
DEFAULT_MAX_WAIT_SECS = 600  # 10 minutes overall ceiling
PROBE_MAX_WAIT_SECS = 300  # Short-mode probe: 5 minutes
SLOW_LOG_AFTER_SECS = 60

# Order to drop filters when a query returns zero — most-likely culprit first.
RELAX_ORDER = [
    "searchQuery",
    "yearsOfExperienceIds",
    "seniorityLevelIds",
    "functionIds",
    "industryIds",
]

SOURCE_SLOW_MESSAGE = (
    "The source is taking longer than usual to respond right now "
    "(this happens occasionally with LinkedIn scraping) - want me to "
    "retry, or try a different anchor keyword?"
)


class ApifyTransientError(Exception):
    """Apify run failed, aborted, timed out, or exceeded our polling ceiling."""

    def __init__(
        self,
        message: str = SOURCE_SLOW_MESSAGE,
        *,
        status: str | None = None,
        run_id: str | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.run_id = run_id


def _token() -> str:
    t = os.environ.get("APIFY_TOKEN")
    if not t:
        raise RuntimeError("APIFY_TOKEN not set")
    return t


def _map_label(m: dict[str, str], label: str, kind: str) -> str:
    key = str(label).strip().lower()
    if key not in m:
        raise ValueError(
            f"unknown {kind} {label!r}. Valid: {sorted(set(m.keys()))}"
        )
    return m[key]


def compile_retrieval(spec: dict, pool_cap: int) -> dict[str, Any]:
    """Compile role-spec retrieval labels into Apify actor input. Ported as-is."""
    r = spec.get("retrieval", {})
    mode = r.get("profileScraperMode") or "Full"
    actor: dict[str, Any] = {
        "profileScraperMode": mode,
        "locations": [r.get("location") or DEFAULT_LOCATION],
    }

    titles = r.get("currentJobTitles") or []
    if titles:
        actor["currentJobTitles"] = titles

    # searchQuery is a literal AND-token matcher — keep it 1-3 domain words or ''
    q = " ".join((r.get("searchQuery") or "").split()[:3]).strip()
    if q:
        actor["searchQuery"] = q

    fns = [_map_label(FUNCTION_MAP, x, "function") for x in (r.get("functions") or [])]
    if fns:
        actor["functionIds"] = fns

    sen = [_map_label(SENIORITY_MAP, x, "seniority") for x in (r.get("seniority") or [])]
    if sen:
        actor["seniorityLevelIds"] = sen

    yrs = [
        _map_label(YEARS_MAP, x, "years of experience")
        for x in (r.get("yearsOfExperience") or [])
    ]
    if yrs:
        actor["yearsOfExperienceIds"] = yrs

    ind = r.get("industryIds") or []
    if ind:
        actor["industryIds"] = [str(x) for x in ind]

    actor["maxItems"] = pool_cap
    actor["takePages"] = max(1, math.ceil(pool_cap / 25))
    return actor


def _run_actor_async(
    actor_input: dict,
    *,
    poll_every: int = POLL_EVERY_SECS,
    max_wait: int = DEFAULT_MAX_WAIT_SECS,
    label: str = "apify",
) -> tuple[list, str, str]:
    """Start an actor run, poll status with short per-request timeouts, fetch items.

    Returns (items, status, run_id) on SUCCEEDED.
    Raises ApifyTransientError on FAILED / ABORTED / TIMED-OUT / ceiling exceeded
    / transport failures that prevent a clean SUCCEEDED outcome.
    """
    token = _token()
    logger.info(
        "%s sending actor input payload: %s",
        label,
        json.dumps(actor_input, default=str),
    )
    try:
        r = requests.post(
            f"https://api.apify.com/v2/acts/{ACTOR}/runs",
            params={"token": token},
            json=actor_input,
            timeout=START_REQUEST_TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("%s start failed: %s", label, type(e).__name__)
        raise ApifyTransientError(
            SOURCE_SLOW_MESSAGE, status="START_FAILED", run_id=None
        ) from e

    run = r.json()["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    logger.info(
        "%s started run_id=%s dataset_id=%s initial_status=%s",
        label,
        run_id,
        dataset_id,
        run.get("status"),
    )

    waited = 0
    status = run.get("status") or "RUNNING"
    info: dict[str, Any] = run
    last_slow_log_at = 0

    while True:
        if waited >= SLOW_LOG_AFTER_SECS and (
            waited - last_slow_log_at >= 60 or last_slow_log_at == 0
        ):
            logger.info(
                "%s poll still running after %ss run_id=%s status=%s "
                "(Apify may be retrying a flaky LinkedIn page)",
                label,
                waited,
                run_id,
                status,
            )
            last_slow_log_at = waited

        try:
            info = requests.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}",
                params={"token": token},
                timeout=POLL_REQUEST_TIMEOUT,
            ).json()["data"]
            status = info["status"]
        except requests.RequestException as e:
            logger.warning(
                "%s poll request error run_id=%s waited=%ss err=%s — will retry",
                label,
                run_id,
                waited,
                type(e).__name__,
            )
            if waited >= max_wait:
                raise ApifyTransientError(
                    SOURCE_SLOW_MESSAGE, status="POLL_TIMEOUT", run_id=run_id
                ) from e
            time.sleep(poll_every)
            waited += poll_every
            continue

        done = status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
        if done or waited >= max_wait:
            break
        time.sleep(poll_every)
        waited += poll_every

    status_message = info.get("statusMessage")
    if waited >= max_wait and status not in (
        "SUCCEEDED",
        "FAILED",
        "ABORTED",
        "TIMED-OUT",
    ):
        status = "MAX_WAIT_EXCEEDED"

    if status != "SUCCEEDED":
        logger.info(
            "%s terminal non-success run_id=%s status=%s statusMessage=%s waited=%ss",
            label,
            run_id,
            status,
            status_message,
            waited,
        )
        raise ApifyTransientError(
            SOURCE_SLOW_MESSAGE, status=status, run_id=run_id
        )

    # Always fetch the dataset belonging to THIS finished run (not a stale id).
    finished_dataset_id = info.get("defaultDatasetId") or dataset_id
    if finished_dataset_id != dataset_id:
        logger.info(
            "%s using finished-run dataset_id=%s (start had %s)",
            label,
            finished_dataset_id,
            dataset_id,
        )
    dataset_id = finished_dataset_id

    try:
        items = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            params={"token": token, "clean": "true", "format": "json"},
            timeout=DATASET_REQUEST_TIMEOUT,
        ).json()
    except requests.RequestException as e:
        logger.warning(
            "%s dataset fetch failed run_id=%s dataset_id=%s err=%s",
            label,
            run_id,
            dataset_id,
            type(e).__name__,
        )
        raise ApifyTransientError(
            SOURCE_SLOW_MESSAGE, status="DATASET_FETCH_FAILED", run_id=run_id
        ) from e

    raw = items or []
    sample_keys = sorted((raw[0] or {}).keys()) if raw else []
    sample_url = (raw[0] or {}).get("linkedinUrl") if raw else None
    logger.info(
        "%s finished run_id=%s dataset_id=%s status=%s statusMessage=%s "
        "raw dataset item count=%s waited=%ss sample_keys=%s sample_linkedinUrl=%s",
        label,
        run_id,
        dataset_id,
        status,
        status_message,
        len(raw) if isinstance(raw, list) else 0,
        waited,
        sample_keys,
        sample_url,
    )
    return raw if isinstance(raw, list) else [], status, run_id


def probe_pool(actor_input: dict, start_page: int = 1) -> tuple[int | None, list]:
    """Cheap pre-flight: ONE search page in Short mode via async start→poll→fetch.

    Returns (total_count_or_None, [short_profiles]) without Full enrichment.
    """
    probe = dict(actor_input)
    probe["profileScraperMode"] = "Short"
    probe["takePages"] = 1
    probe["startPage"] = max(1, int(start_page))
    probe.pop("maxItems", None)

    items, status, run_id = _run_actor_async(
        probe,
        poll_every=POLL_EVERY_SECS,
        max_wait=PROBE_MAX_WAIT_SECS,
        label="probe_pool",
    )
    raw_count = len(items)
    count = None
    if items:
        count = (
            ((items[0].get("_meta") or {}).get("pagination") or {}).get(
                "totalElements"
            )
        )
    logger.info(
        "probe_pool raw dataset item count=%s pool_n(totalElements)=%s "
        "status=%s run_id=%s startPage=%s",
        raw_count,
        count,
        status,
        run_id,
        probe.get("startPage"),
    )
    return count, items


def probe_with_relax(
    actor_input: dict, start_page: int = 1
) -> tuple[int | None, list, dict]:
    """Probe; if 0 hits, drop filters one at a time in RELAX_ORDER and re-probe.

    Returns (pool_n, preview, effective_actor_input). Uses a copy so the
    original input is unchanged; caller gets the relaxed input for later pages.
    """
    working = dict(actor_input)
    logger.info(
        "probe_with_relax start start_page=%s actor_input=%s",
        start_page,
        json.dumps(working, default=str),
    )
    pool_n, preview = probe_pool(working, start_page=start_page)
    logger.info(
        "probe_with_relax initial result pool_n=%s preview_len=%s",
        pool_n,
        len(preview) if preview else 0,
    )
    for key in RELAX_ORDER:
        if preview:
            break
        if key in working:
            dropped = working.pop(key)
            logger.info(
                "probe_with_relax relaxing drop key=%s value=%s remaining=%s",
                key,
                json.dumps(dropped, default=str),
                json.dumps(working, default=str),
            )
            pool_n, preview = probe_pool(working, start_page=start_page)
            logger.info(
                "probe_with_relax after drop %s: pool_n=%s preview_len=%s",
                key,
                pool_n,
                len(preview) if preview else 0,
            )
    logger.info(
        "probe_with_relax done pool_n=%s preview_len=%s effective_input=%s",
        pool_n,
        len(preview) if preview else 0,
        json.dumps(working, default=str),
    )
    return pool_n, preview, working


def fetch_profiles(
    actor_input: dict,
    poll_every: int = POLL_EVERY_SECS,
    max_wait: int = DEFAULT_MAX_WAIT_SECS,
) -> tuple[list, str, str]:
    """Async run: start -> poll -> fetch dataset. Ported from contra6_source2.

    Returns (items, status, run_id) on SUCCEEDED.
    Raises ApifyTransientError on failed / aborted / ceiling exceeded.
    """
    return _run_actor_async(
        actor_input,
        poll_every=poll_every,
        max_wait=max_wait,
        label="fetch_profiles",
    )


def fetch_profiles_by_urls(
    urls: list[str],
    mode: str = "Full",
    poll_every: int = POLL_EVERY_SECS,
    max_wait: int = DEFAULT_MAX_WAIT_SECS,
) -> tuple[list, str, str]:
    """Full (or Full+email) enrichment targeted at specific LinkedIn profile URLs.

    Only call after role_candidates dedup — never enrich already-seen URLs for
    this role.
    """
    actor_input = {
        "profileScraperMode": mode,
        "profileUrls": urls,
        "maxItems": len(urls),
        "takePages": max(1, math.ceil(len(urls) / 25)),
    }
    return fetch_profiles(actor_input, poll_every=poll_every, max_wait=max_wait)


def recover_last_dataset() -> list:
    """Pull the most recent SUCCEEDED run's dataset for this actor."""
    token = _token()
    r = requests.get(
        f"https://api.apify.com/v2/acts/{ACTOR}/runs/last/dataset/items",
        params={
            "token": token,
            "status": "SUCCEEDED",
            "clean": "true",
            "format": "json",
        },
        timeout=DATASET_REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    return r.json() or []


def current_role(p: dict) -> tuple[str, str]:
    for exp in p.get("experience", []) or []:
        if (exp.get("endDate") or {}).get("text") == "Present":
            return exp.get("position", "") or "", exp.get("companyName", "") or ""
    # Full profiles use currentPosition; Short search hits use currentPositions.
    cp_list = p.get("currentPosition") or p.get("currentPositions") or []
    cp = cp_list[0] if isinstance(cp_list, list) and cp_list else {}
    if not isinstance(cp, dict):
        cp = {}
    exp0 = (p.get("experience") or [{}])[0]
    if not isinstance(exp0, dict):
        exp0 = {}
    company = (
        cp.get("companyName")
        or exp0.get("companyName")
        or ((cp.get("company") or {}).get("name") if isinstance(cp.get("company"), dict) else "")
        or ""
    )
    title = (
        exp0.get("position")
        or cp.get("position")
        or cp.get("title")
        or ""
    )
    return title or "", company or ""


def location_str(p: dict) -> str:
    parsed = (p.get("location") or {}).get("parsed") or {}
    city, country = parsed.get("city") or "", parsed.get("country") or ""
    return ", ".join([x for x in (city, country) if x]) or (
        (p.get("location") or {}).get("linkedinText", "") or ""
    )


def top_skills(p: dict) -> str:
    if p.get("topSkills"):
        return (
            p["topSkills"]
            if isinstance(p["topSkills"], str)
            else " • ".join(str(x) for x in p["topSkills"])
        )
    return " • ".join(
        s.get("name", "") for s in (p.get("skills") or [])[:6] if s.get("name")
    )


def compact(p: dict, idx: int = 0) -> dict:
    """Shape a profile for UI table columns only — never persist this dict.

    Flattened display fields (name, title, company, location, headline, skills)
    are derived FROM the full Apify item. The untouched Apify item itself must
    be stored separately as candidates.raw_profile.
    """
    title, company = current_role(p)
    exps = [
        f"{e.get('position', '')} @ {e.get('companyName', '')} ({e.get('duration', '')})"
        for e in (p.get("experience") or [])[:3]
    ]
    return {
        "idx": idx,
        "firstName": p.get("firstName", "") or "",
        "lastName": p.get("lastName", "") or "",
        "headline": p.get("headline", "") or "",
        "current_title": title,
        "current_company": company,
        "location": location_str(p),
        "linkedinUrl": p.get("linkedinUrl", "") or "",
        "topSkills": top_skills(p),
        "_experience": " | ".join(exps),
        "_about": (p.get("about") or "")[:400],
    }
