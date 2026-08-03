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
RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"

# Order to drop filters when a query returns zero — most-likely culprit first.
RELAX_ORDER = [
    "searchQuery",
    "yearsOfExperienceIds",
    "seniorityLevelIds",
    "functionIds",
    "industryIds",
]


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


def _apify_header(headers: Any, *names: str) -> str | None:
    """Case-insensitive lookup across common Apify response header spellings."""
    lower = {str(k).lower(): v for k, v in headers.items()}
    for name in names:
        v = lower.get(name.lower())
        if v:
            return str(v)
    return None


def probe_pool(actor_input: dict, start_page: int = 1) -> tuple[int | None, list]:
    """Cheap pre-flight: ONE search page in Short mode.

    Returns (total_count_or_None, [short_profiles]) without Full enrichment.
    Ported from contra6_source2.probe_pool; start_page added for pagination.
    """
    probe = dict(actor_input)
    probe["profileScraperMode"] = "Short"
    probe["takePages"] = 1
    probe["startPage"] = max(1, int(start_page))
    probe.pop("maxItems", None)
    logger.info(
        "probe_pool sending actor input payload: %s",
        json.dumps(probe, default=str),
    )
    r = requests.post(
        RUN_SYNC_URL, params={"token": _token()}, json=probe, timeout=180
    )
    run_id = _apify_header(
        r.headers, "x-apify-run-id", "X-Apify-Run-Id", "x-apify-actor-run-id"
    )
    http_status = r.status_code
    pagination_total = _apify_header(r.headers, "x-apify-pagination-total")
    logger.info(
        "probe_pool Apify response http_status=%s run_id=%s "
        "X-Apify-Pagination-Total=%s",
        http_status,
        run_id or "(none in headers; sync endpoint)",
        pagination_total,
    )
    r.raise_for_status()
    items = r.json() or []
    raw_count = len(items) if isinstance(items, list) else 0
    count = None
    if items:
        count = (
            ((items[0].get("_meta") or {}).get("pagination") or {}).get(
                "totalElements"
            )
        )
    # Sync endpoint has no separate run status field — HTTP 2xx => request ok.
    # When the dataset is empty, look up the latest actor run for statusMessage
    # (e.g. free-tier limits that still return HTTP 201 + []).
    status_message = None
    latest_run_id = run_id
    latest_status = "SUCCEEDED" if 200 <= http_status < 300 else f"HTTP_{http_status}"
    if raw_count == 0:
        try:
            rr = requests.get(
                f"https://api.apify.com/v2/acts/{ACTOR}/runs",
                params={"token": _token(), "limit": 1, "desc": 1},
                timeout=30,
            )
            if rr.ok:
                latest = ((rr.json().get("data") or {}).get("items") or [None])[0]
                if latest:
                    latest_run_id = latest.get("id") or latest_run_id
                    latest_status = latest.get("status") or latest_status
                    info = requests.get(
                        f"https://api.apify.com/v2/actor-runs/{latest_run_id}",
                        params={"token": _token()},
                        timeout=30,
                    ).json().get("data") or {}
                    status_message = info.get("statusMessage")
        except Exception as e:  # noqa: BLE001 — best-effort diagnostics only
            logger.info("probe_pool could not fetch latest run statusMessage: %s", e)
    logger.info(
        "probe_pool raw dataset item count=%s pool_n(totalElements)=%s "
        "status=%s statusMessage=%s run_id=%s startPage=%s",
        raw_count,
        count,
        latest_status,
        status_message,
        latest_run_id or "(sync/unknown)",
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
    actor_input: dict, poll_every: int = 6, max_wait: int = 1800
) -> tuple[list, str, str]:
    """Async run: start -> poll -> fetch dataset. Ported from contra6_source2.

    Returns (items, status, run_id).
    """
    token = _token()
    logger.info(
        "fetch_profiles sending actor input payload: %s",
        json.dumps(actor_input, default=str),
    )
    r = requests.post(
        f"https://api.apify.com/v2/acts/{ACTOR}/runs",
        params={"token": token},
        json=actor_input,
        timeout=60,
    )
    r.raise_for_status()
    run = r.json()["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    logger.info(
        "fetch_profiles started run_id=%s dataset_id=%s initial_status=%s",
        run_id,
        dataset_id,
        run.get("status"),
    )

    waited = 0
    status = "RUNNING"
    while True:
        info = requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            params={"token": token},
            timeout=60,
        ).json()["data"]
        status = info["status"]
        done = status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT")
        if done or waited >= max_wait:
            break
        time.sleep(poll_every)
        waited += poll_every

    status_message = info.get("statusMessage")
    items = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        params={"token": token, "clean": "true", "format": "json"},
        timeout=180,
    ).json()
    raw = items or []
    logger.info(
        "fetch_profiles finished run_id=%s status=%s statusMessage=%s "
        "raw dataset item count=%s",
        run_id,
        status,
        status_message,
        len(raw) if isinstance(raw, list) else 0,
    )
    return raw, status, run_id


def fetch_profiles_by_urls(
    urls: list[str], mode: str = "Full", poll_every: int = 6, max_wait: int = 1800
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
        timeout=180,
    )
    r.raise_for_status()
    return r.json() or []


def current_role(p: dict) -> tuple[str, str]:
    for exp in p.get("experience", []) or []:
        if (exp.get("endDate") or {}).get("text") == "Present":
            return exp.get("position", "") or "", exp.get("companyName", "") or ""
    cp = (p.get("currentPosition") or [{}])[0]
    exp0 = (p.get("experience") or [{}])[0]
    return (
        exp0.get("position", "") or "",
        cp.get("companyName", exp0.get("companyName", "")) or "",
    )


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
    """Profile shaping for display/storage — ported from contra6_source2.compact."""
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
