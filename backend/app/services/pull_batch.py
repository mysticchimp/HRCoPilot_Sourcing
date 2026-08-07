"""pull_batch — Short probe → dedupe → Full enrich only new URLs → store."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.apify.client import (
    SOURCE_SLOW_MESSAGE,
    ApifyTransientError,
    compact,
    compile_retrieval,
    fetch_profiles,
    probe_pool,
    probe_with_relax,
)
from app.models import Candidate, PullBatch, Role, RoleCandidate

logger = logging.getLogger("sourcing.pull")

# Transient LinkedIn/Apify timeouts on Full mode — retry before Short fallback.
FULL_ENRICH_RETRIES = 2
FULL_ENRICH_RETRY_BACKOFF_SECS = 5

# Cap retry-incomplete Full attempts per candidate. After this, skip auto-retry
# (UI: "failed after N attempts") so stuck ACwAA stubs don't burn Apify forever.
MAX_ENRICH_RETRY_ATTEMPTS = 3

INCOMPLETE_REASON = "insufficient data — not scored (thin Short profile)"


def _enrich_retry_count(cand: Candidate) -> int:
    return int(getattr(cand, "enrich_retry_count", 0) or 0)


def enrich_status(cand: Candidate) -> str:
    """complete | needs_re_pull | enrich_failed (exhausted auto-retries)."""
    if bool(getattr(cand, "is_complete_profile", True)):
        return "complete"
    if _enrich_retry_count(cand) >= MAX_ENRICH_RETRY_ATTEMPTS:
        return "enrich_failed"
    return "needs_re_pull"


def is_enrich_retry_exhausted(cand: Candidate) -> bool:
    return enrich_status(cand) == "enrich_failed"

# Pagination / mode are run-specific; filter snapshot keeps search constraints only.
_ACTOR_RUN_KEYS = frozenset(
    {"startPage", "takePages", "maxItems", "profileScraperMode"}
)


def _filter_snapshot(actor_input: dict) -> dict[str, Any]:
    return {k: v for k, v in actor_input.items() if k not in _ACTOR_RUN_KEYS}


def _dropped_actor_keys(original: dict, effective: dict) -> list[str]:
    return [k for k in original if k not in effective]


def _set_effective_actor_input(
    role: Role, original: dict, effective: dict
) -> None:
    """Record which filters worked after probe_with_relax (does not alter relax)."""
    dropped = _dropped_actor_keys(original, effective)
    role.effective_actor_input = {
        "dropped_keys": dropped,
        "actor_input": _filter_snapshot(effective),
    }
    logger.info(
        "persisted effective_actor_input role_id=%s dropped_keys=%s",
        role.id,
        dropped,
    )


def _resolve_retry_effective_input(
    role: Role, compiled: dict
) -> tuple[dict[str, Any], list[str], bool]:
    """Pick working filters for retry-incomplete.

    Prefer role.effective_actor_input from a prior pull; otherwise run the same
    probe_with_relax path as the original pull (try full set, then drop
    searchQuery first, etc.).
    """
    stored = (
        role.effective_actor_input
        if isinstance(role.effective_actor_input, dict)
        else None
    )
    stored_input = (stored or {}).get("actor_input")
    if isinstance(stored_input, dict) and stored_input:
        effective = dict(stored_input)
        dropped = list(
            stored.get("dropped_keys")
            if isinstance(stored.get("dropped_keys"), list)
            else _dropped_actor_keys(compiled, effective)
        )
        logger.info(
            "retry_incomplete using persisted effective filters dropped=%s",
            dropped,
        )
        return effective, dropped, True

    _pool_n, preview, effective = probe_with_relax(dict(compiled), start_page=1)
    dropped = _dropped_actor_keys(compiled, effective)
    _set_effective_actor_input(role, compiled, effective)
    logger.info(
        "retry_incomplete probe_with_relax done pool_n=%s preview_len=%s "
        "dropped=%s effective=%s",
        _pool_n,
        len(preview) if preview else 0,
        dropped,
        json.dumps(_filter_snapshot(effective), default=str),
    )
    return effective, dropped, False


def _build_retry_full_input(
    effective: dict,
    *,
    mode: str,
    last_page: int,
    target_count: int,
) -> dict[str, Any]:
    full_input = dict(effective)
    full_input["profileScraperMode"] = mode
    full_input["startPage"] = 1
    full_input["takePages"] = max(1, last_page)
    full_input["maxItems"] = max(target_count * 2, 25)
    return full_input


def _transient_result(
    *,
    pages_scanned: int = 0,
    status: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "candidates": [],
        "summary": SOURCE_SLOW_MESSAGE,
        "batch_id": None,
        "pages_scanned": pages_scanned,
        "error": "apify_transient",
        "status": status,
        "apify_run_id": run_id,
    }


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().split("?")[0].rstrip("/")
    return u or None


# Short search returns Sales-Nav ids (ACwAA…); Full returns member ids (ACoAA…).
# After the AC[ow]AA prefix they share a stable 7-char stem for the same person.
# NEVER derive a stem from public vanity slugs — first-7-char prefixes collide
# across different people (e.g. abdullah-al-zadjali vs abdullahkhere → "abdulla").
_LINKEDIN_MEMBER_ID_RE = re.compile(r"^AC[ow]AA(.{7})", re.IGNORECASE)


def _linkedin_member_stem(value: str | None) -> str | None:
    """Stable 7-char stem shared by ACwAA (Short) and ACoAA (Full) member ids.

    Returns None for public vanity slugs. Vanity URLs are not identity keys;
    dedup those via exact linkedin_url only.
    """
    if not value:
        return None
    s = str(value).strip()
    if "/in/" in s:
        s = s.split("/in/")[-1].split("?")[0].rstrip("/")
    m = _LINKEDIN_MEMBER_ID_RE.match(s)
    return m.group(1) if m else None


def _is_complete_apify_profile(profile: dict | None) -> bool:
    """True when the Apify item carries Full-mode substance (not a Short stub)."""
    if not profile or not isinstance(profile, dict):
        return False
    exp = profile.get("experience")
    if isinstance(exp, list) and len(exp) > 0:
        return True
    skills = profile.get("skills")
    if isinstance(skills, list) and len(skills) > 0:
        return True
    about = profile.get("about")
    if isinstance(about, str) and about.strip():
        return True
    return False


def _index_full_profiles(
    profiles: list,
    by_url: dict[str, dict],
    by_id: dict[str, dict],
    by_stem: dict[str, dict] | None = None,
) -> int:
    """Merge Full items into URL/id/stem indexes. Returns count missing linkedinUrl."""
    missing_url = 0
    for p in profiles or []:
        if not isinstance(p, dict):
            continue
        u = _normalize_url(p.get("linkedinUrl"))
        if u:
            by_url[u] = p
        else:
            missing_url += 1
        pid = p.get("id") or p.get("profileIdInSearch")
        if pid is not None:
            by_id[str(pid)] = p
        if by_stem is not None:
            for raw in (pid, u, p.get("linkedinUrl")):
                stem = _linkedin_member_stem(
                    str(raw) if raw is not None else None
                )
                if stem and stem not in by_stem:
                    by_stem[stem] = p
    return missing_url


def _resolve_full_profile(
    url: str,
    *,
    by_url: dict[str, dict],
    by_id: dict[str, dict],
    short_by_url: dict[str, dict],
    by_stem: dict[str, dict] | None = None,
) -> dict | None:
    profile = by_url.get(url)
    if profile and _is_complete_apify_profile(profile):
        return profile
    short = short_by_url.get(url) or {}
    sid = short.get("id") or short.get("profileIdInSearch")
    if sid is not None:
        by_id_hit = by_id.get(str(sid))
        if by_id_hit and _is_complete_apify_profile(by_id_hit):
            return by_id_hit
    # Short ACwAA… vs Full ACoAA… — match on shared member stem.
    if by_stem:
        for raw in (sid, url, short.get("linkedinUrl")):
            stem = _linkedin_member_stem(str(raw) if raw is not None else None)
            if not stem:
                continue
            stem_hit = by_stem.get(stem)
            if stem_hit and _is_complete_apify_profile(stem_hit):
                return stem_hit
    return None

def _retry_full_enrich_for_missing(
    *,
    full_input: dict,
    missing_urls: list[str],
    by_url: dict[str, dict],
    by_id: dict[str, dict],
    short_by_url: dict[str, dict],
    by_stem: dict[str, dict] | None = None,
) -> tuple[str | None, str | None]:
    """Re-run Full search up to FULL_ENRICH_RETRIES times for unresolved URLs.

    Batches retries (same search filters/page) — per-URL profileUrls enrich does
    not work for Sales-Nav /in/ACwAA… links. Returns (last_status, last_run_id).
    """
    last_status: str | None = None
    last_run_id: str | None = None
    pending = list(missing_urls)
    for attempt in range(1, FULL_ENRICH_RETRIES + 1):
        if not pending:
            break
        logger.info(
            "pull_batch Full enrich retry %s/%s for %s unresolved URLs "
            "(backoff=%ss)",
            attempt,
            FULL_ENRICH_RETRIES,
            len(pending),
            FULL_ENRICH_RETRY_BACKOFF_SECS,
        )
        time.sleep(FULL_ENRICH_RETRY_BACKOFF_SECS)
        try:
            profiles, last_status, last_run_id = fetch_profiles(full_input)
        except ApifyTransientError as e:
            logger.warning(
                "pull_batch Full enrich retry %s failed transiently status=%s "
                "run_id=%s",
                attempt,
                e.status,
                e.run_id,
            )
            last_status = e.status
            last_run_id = e.run_id
            continue
        _index_full_profiles(profiles, by_url, by_id, by_stem)
        logger.info(
            "pull_batch Full enrich retry %s done run_id=%s status=%s "
            "raw_count=%s",
            attempt,
            last_run_id,
            last_status,
            len(profiles) if profiles else 0,
        )
        still: list[str] = []
        for url in pending:
            if _resolve_full_profile(
                url,
                by_url=by_url,
                by_id=by_id,
                short_by_url=short_by_url,
                by_stem=by_stem,
            ):
                logger.info(
                    "pull_batch Full enrich retry recovered url=%s attempt=%s",
                    url,
                    attempt,
                )
            else:
                still.append(url)
        pending = still
    return last_status, last_run_id


def _seen_urls_for_role(db: Session, role_id: uuid.UUID) -> set[str]:
    rows = (
        db.execute(
            select(Candidate.linkedin_url)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(RoleCandidate.role_id == role_id)
        )
        .scalars()
        .all()
    )
    return {_normalize_url(u) for u in rows if u}


def _next_batch_number(db: Session, role_id: uuid.UUID) -> int:
    current = db.execute(
        select(func.coalesce(func.max(PullBatch.batch_number), 0)).where(
            PullBatch.role_id == role_id,
            PullBatch.batch_number > 0,
        )
    ).scalar()
    return int(current or 0) + 1


def _next_probe_batch_number(db: Session, role_id: uuid.UUID) -> int:
    """Negative batch_numbers reserved for Short-mode probe audit rows."""
    current = db.execute(
        select(func.coalesce(func.min(PullBatch.batch_number), 0)).where(
            PullBatch.role_id == role_id,
            PullBatch.batch_number < 0,
        )
    ).scalar()
    return int(current or 0) - 1


def _log_apify_call(
    db: Session,
    *,
    role_id: uuid.UUID,
    batch_number: int,
    apify_run_id: str | None,
    params_snapshot: dict,
) -> PullBatch:
    row = PullBatch(
        id=uuid.uuid4(),
        role_id=role_id,
        batch_number=batch_number,
        apify_run_id=apify_run_id,
        params_snapshot=params_snapshot,
    )
    db.add(row)
    db.flush()
    return row


def _find_candidate_by_linkedin_stem(
    db: Session, stem: str
) -> Candidate | None:
    """Find a candidate whose ACwAA/ACoAA member id shares ``stem``.

    ``stem`` must come from ``_linkedin_member_stem`` (member ids only).
    Public vanity slug URLs never participate — their stem is always None.
    """
    if not stem:
        return None
    # Sales-Nav / member-id URLs embed ACwAA… / ACoAA…; stem is the 7-char body.
    # Restrict the prefilter to those URL shapes so vanity slugs that happen to
    # contain the same 7 chars are never loaded or matched.
    rows = (
        db.execute(
            select(Candidate).where(
                Candidate.linkedin_url.contains(stem),
                or_(
                    Candidate.linkedin_url.ilike("%/in/ACwAA%"),
                    Candidate.linkedin_url.ilike("%/in/ACoAA%"),
                ),
            )
        )
        .scalars()
        .all()
    )
    for cand in rows:
        if _linkedin_member_stem(cand.linkedin_url) == stem:
            return cand
        raw = cand.raw_profile if isinstance(cand.raw_profile, dict) else {}
        rid = raw.get("id") or raw.get("profileIdInSearch")
        if _linkedin_member_stem(str(rid) if rid is not None else None) == stem:
            return cand
    return None


def _upsert_candidate(
    db: Session,
    raw_apify_item: dict,
    display: dict,
    *,
    is_complete: bool,
    existing: Candidate | None = None,
) -> Candidate:
    """Persist one candidate.

    raw_apify_item — untouched Apify dataset item → raw_profile JSONB.
    display — compact() output used ONLY for flattened UI columns.
    is_complete — False when storing a Short stub after Full enrich failed.
    existing — when set (e.g. retry-incomplete), update that row even if the
    Full profile's public slug URL differs from the stored Sales-Nav ACwAA URL.
    """
    url = _normalize_url(display.get("linkedinUrl")) or _normalize_url(
        raw_apify_item.get("linkedinUrl")
    )
    if not url and existing is not None:
        url = _normalize_url(existing.linkedin_url)
    if not url:
        raise ValueError("profile missing linkedinUrl")

    # Guard: never accidentally persist the compact()/display dict as raw_profile.
    if set(raw_apify_item.keys()) <= {
        "idx",
        "firstName",
        "lastName",
        "headline",
        "current_title",
        "current_company",
        "location",
        "linkedinUrl",
        "topSkills",
        "_experience",
        "_about",
    }:
        logger.warning(
            "_upsert_candidate refused compact-shaped dict as raw_profile url=%s",
            url,
        )

    about = raw_apify_item.get("about")
    exp = raw_apify_item.get("experience")
    logger.info(
        "_upsert_candidate url=%s is_complete=%s raw_keys=%s about_chars=%s "
        "experience_len=%s education_len=%s skills_len=%s",
        url,
        is_complete,
        sorted(raw_apify_item.keys())[:24],
        len(about) if isinstance(about, str) else (0 if about is None else -1),
        len(exp) if isinstance(exp, list) else 0,
        len(raw_apify_item.get("education") or [])
        if isinstance(raw_apify_item.get("education"), list)
        else 0,
        len(raw_apify_item.get("skills") or [])
        if isinstance(raw_apify_item.get("skills"), list)
        else 0,
    )

    if existing is None:
        existing = db.execute(
            select(Candidate).where(Candidate.linkedin_url == url)
        ).scalar_one_or_none()
    if existing is None:
        # Full public-slug URL vs stored Sales-Nav /in/ACwAA… URL.
        stem = _linkedin_member_stem(url) or _linkedin_member_stem(
            str(raw_apify_item.get("id") or raw_apify_item.get("profileIdInSearch") or "")
            or None
        )
        if stem:
            existing = _find_candidate_by_linkedin_stem(db, stem)
            if existing:
                logger.info(
                    "_upsert_candidate matched existing via stem=%s "
                    "stored_url=%s full_url=%s",
                    stem,
                    existing.linkedin_url,
                    url,
                )

    if existing:
        # Prefer the public slug URL when it does not collide with another row.
        if url != existing.linkedin_url:
            conflict = db.execute(
                select(Candidate).where(
                    Candidate.linkedin_url == url,
                    Candidate.id != existing.id,
                )
            ).scalar_one_or_none()
            if conflict is None:
                existing.linkedin_url = url
            else:
                logger.info(
                    "_upsert_candidate keeping stored url=%s; slug=%s owned by %s",
                    existing.linkedin_url,
                    url,
                    conflict.id,
                )
        existing.first_name = display.get("firstName") or existing.first_name
        existing.last_name = display.get("lastName") or existing.last_name
        existing.headline = display.get("headline") or existing.headline
        existing.current_title = display.get("current_title") or existing.current_title
        existing.current_company = (
            display.get("current_company") or existing.current_company
        )
        existing.location = display.get("location") or existing.location
        existing.top_skills = display.get("topSkills") or existing.top_skills
        # Prefer complete Full profiles; never downgrade a complete row to Short.
        if is_complete or not existing.is_complete_profile:
            existing.raw_profile = raw_apify_item
            existing.is_complete_profile = is_complete
            if is_complete:
                existing.enrich_retry_count = 0
        db.flush()
        return existing

    cand = Candidate(
        id=uuid.uuid4(),
        linkedin_url=url,
        first_name=display.get("firstName"),
        last_name=display.get("lastName"),
        headline=display.get("headline"),
        current_title=display.get("current_title"),
        current_company=display.get("current_company"),
        location=display.get("location"),
        top_skills=display.get("topSkills"),
        raw_profile=raw_apify_item,
        is_complete_profile=is_complete,
        enrich_retry_count=0,
        first_seen_at=datetime.now(timezone.utc),
    )
    db.add(cand)
    db.flush()
    return cand


def _candidate_row(c: Candidate) -> dict:
    status = enrich_status(c)
    return {
        "id": str(c.id),
        "linkedin_url": c.linkedin_url,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "headline": c.headline,
        "current_title": c.current_title,
        "current_company": c.current_company,
        "location": c.location,
        "top_skills": c.top_skills,
        "is_complete_profile": bool(c.is_complete_profile),
        "enrich_retry_count": _enrich_retry_count(c),
        "enrich_status": status,
        "enrich_retry_exhausted": status == "enrich_failed",
        "max_enrich_retry_attempts": MAX_ENRICH_RETRY_ATTEMPTS,
    }


def pull_batch(db: Session, role_id: uuid.UUID, batch_size: int = 25) -> dict[str, Any]:
    """Probe Short pages, dedupe against role_candidates, Full-enrich only new URLs."""
    role = db.get(Role, role_id)
    if not role:
        raise ValueError(f"role {role_id} not found")

    retrieval = dict(role.retrieval or {})
    pool_cap = int(retrieval.get("pool_cap") or batch_size)
    batch_size = max(1, min(int(batch_size), 150))
    pages_scanned = 0

    try:
        return _pull_batch_inner(
            db,
            role=role,
            role_id=role_id,
            retrieval=retrieval,
            pool_cap=pool_cap,
            batch_size=batch_size,
        )
    except ApifyTransientError as e:
        logger.info(
            "pull_batch Apify transient failure status=%s run_id=%s — "
            "returning friendly message (not a raw exception)",
            e.status,
            e.run_id,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _transient_result(pages_scanned=pages_scanned, status=e.status, run_id=e.run_id)


def _pull_batch_inner(
    db: Session,
    *,
    role: Role,
    role_id: uuid.UUID,
    retrieval: dict,
    pool_cap: int,
    batch_size: int,
) -> dict[str, Any]:
    logger.info(
        "pull_batch called role_id=%s slug=%s batch_size=%s pool_cap=%s "
        "last_page=%s retrieval params for compile_retrieval: %s",
        role_id,
        role.slug,
        batch_size,
        pool_cap,
        role.last_page,
        json.dumps({"retrieval": retrieval}, default=str),
    )

    actor_input = compile_retrieval({"retrieval": retrieval}, pool_cap)
    logger.info(
        "pull_batch compiled actor_input (pre-probe): %s",
        json.dumps(actor_input, default=str),
    )

    page = int(role.last_page or 0) + 1
    new_urls: list[str] = []
    pages_scanned = 0
    skipped_repeats = 0
    missing_linkedin_url = 0
    seen = _seen_urls_for_role(db, role_id)
    # Keep Short-mode profiles so we can store them if Full enrich returns 0.
    short_by_url: dict[str, dict] = {}
    effective_input = dict(actor_input)
    first_page = True
    max_pages = 40
    logger.info(
        "pull_batch start pagination page=%s seen_urls=%s max_pages=%s",
        page,
        len(seen),
        max_pages,
    )

    while len(new_urls) < batch_size and pages_scanned < max_pages:
        if first_page:
            _pool_n, preview, effective_input = probe_with_relax(
                effective_input, start_page=page
            )
            first_page = False
            # Persist working filters so retry-incomplete / re-pulls can reuse them.
            _set_effective_actor_input(role, actor_input, effective_input)
        else:
            _pool_n, preview = probe_pool(effective_input, start_page=page)

        raw_preview_count = len(preview) if preview else 0
        logger.info(
            "pull_batch AFTER probe page=%s raw_preview_count=%s pool_n=%s "
            "seen_urls=%s new_urls_so_far=%s (dedup input sizes)",
            page,
            raw_preview_count,
            _pool_n,
            len(seen),
            len(new_urls),
        )

        _log_apify_call(
            db,
            role_id=role_id,
            batch_number=_next_probe_batch_number(db, role_id),
            apify_run_id=None,
            params_snapshot={
                **effective_input,
                "profileScraperMode": "Short",
                "page": page,
                "probe": True,
                "pool_n": _pool_n,
                "raw_preview_count": raw_preview_count,
            },
        )
        pages_scanned += 1

        if not preview:
            logger.info(
                "pull_batch empty preview on page=%s — stopping page scan",
                page,
            )
            break

        page_new = 0
        page_skip = 0
        page_missing = 0
        for item in preview:
            url = _normalize_url(item.get("linkedinUrl"))
            if not url:
                page_missing += 1
                missing_linkedin_url += 1
                continue
            short_by_url[url] = item
            if url in seen or url in new_urls:
                skipped_repeats += 1
                page_skip += 1
                continue
            new_urls.append(url)
            page_new += 1
            if len(new_urls) >= batch_size:
                break

        logger.info(
            "pull_batch AFTER dedup page=%s preview=%s extracted_new=%s "
            "skipped_repeats_page=%s missing_linkedinUrl_page=%s "
            "new_urls_total=%s skipped_repeats_total=%s short_by_url=%s",
            page,
            raw_preview_count,
            page_new,
            page_skip,
            page_missing,
            len(new_urls),
            skipped_repeats,
            len(short_by_url),
        )

        if len(new_urls) >= batch_size:
            break
        page += 1

    target_urls = [u for u in new_urls[:batch_size] if u not in seen]
    logger.info(
        "pull_batch target_urls for Full enrich=%s (from new_urls=%s "
        "seen=%s missing_linkedinUrl_total=%s)",
        len(target_urls),
        len(new_urls),
        len(seen),
        missing_linkedin_url,
    )

    if not target_urls:
        role.last_page = page
        role.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "returning 0 candidates to UI (no new URLs after probe/dedup; "
            "skipped_repeats=%s pages_scanned=%s missing_linkedinUrl=%s) "
            "commit=ok",
            skipped_repeats,
            pages_scanned,
            missing_linkedin_url,
        )
        return {
            "candidates": [],
            "summary": (
                f"Pulled 0 new (skipped {skipped_repeats} repeats across "
                f"{pages_scanned} pages)."
            ),
            "batch_id": None,
            "pages_scanned": pages_scanned,
            "incomplete_count": 0,
        }

    mode = retrieval.get("profileScraperMode") or "Full"
    # Full scrape of the SAME search filters/page (original CLI pattern).
    # profileUrls enrich returns "no query" for Sales-Nav-style /in/ACwAA… URLs
    # and never yields about/experience — so we must not rely on it for raw_profile.
    full_input = dict(effective_input)
    full_input["profileScraperMode"] = mode
    full_input["startPage"] = page
    full_input["takePages"] = 1
    full_input["maxItems"] = max(batch_size, len(target_urls))
    logger.info(
        "pull_batch starting Full search enrich page=%s mode=%s "
        "target_urls=%s full_input=%s",
        page,
        mode,
        len(target_urls),
        json.dumps(full_input, default=str),
    )
    profiles, status, run_id = fetch_profiles(full_input)
    logger.info(
        "pull_batch Full search done run_id=%s status=%s raw profile count=%s "
        "(expected ~%s)",
        run_id,
        status,
        len(profiles) if profiles else 0,
        len(target_urls),
    )

    by_url: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    by_stem: dict[str, dict] = {}
    full_missing_url = _index_full_profiles(profiles, by_url, by_id, by_stem)
    logger.info(
        "pull_batch Full search URL index size=%s id_index=%s stem_index=%s "
        "missing_linkedinUrl=%s target_urls=%s overlap_by_url=%s",
        len(by_url),
        len(by_id),
        len(by_stem),
        full_missing_url,
        len(target_urls),
        len(set(by_url) & set(target_urls)),
    )

    unresolved = [
        u
        for u in target_urls
        if not _resolve_full_profile(
            u,
            by_url=by_url,
            by_id=by_id,
            short_by_url=short_by_url,
            by_stem=by_stem,
        )
    ]
    if unresolved:
        retry_status, retry_run_id = _retry_full_enrich_for_missing(
            full_input=full_input,
            missing_urls=unresolved,
            by_url=by_url,
            by_id=by_id,
            short_by_url=short_by_url,
            by_stem=by_stem,
        )
        if retry_run_id:
            run_id = retry_run_id
        if retry_status:
            status = retry_status

    batch_number = _next_batch_number(db, role_id)
    batch = _log_apify_call(
        db,
        role_id=role_id,
        batch_number=batch_number,
        apify_run_id=run_id,
        params_snapshot={
            "profileScraperMode": mode,
            "page": page,
            "full_search": True,
            "maxItems": full_input.get("maxItems"),
            "retrieval": retrieval,
            "actor_status": status,
            "probe_pages": pages_scanned,
            "full_enrich_raw_count": len(by_url),
            "full_enrich_retries": FULL_ENRICH_RETRIES,
            "actor_input": full_input,
        },
    )

    stored: list[Candidate] = []
    upserted = 0
    role_candidate_inserts = 0
    used_short_fallback = 0
    skipped_no_profile = 0
    for i, url in enumerate(target_urls):
        profile = _resolve_full_profile(
            url,
            by_url=by_url,
            by_id=by_id,
            short_by_url=short_by_url,
            by_stem=by_stem,
        )
        is_complete = True
        if not profile:
            # Last resort: Short hit — incomplete for ML (no about/experience).
            profile = short_by_url.get(url)
            is_complete = False
        if not profile:
            skipped_no_profile += 1
            logger.info(
                "pull_batch no Full or Short profile for target url=%s",
                url,
            )
            continue
        if not is_complete:
            used_short_fallback += 1
            logger.warning(
                "pull_batch storing Short fallback as incomplete raw_profile "
                "url=%s — Full search miss after retries",
                url,
            )

        display = compact(profile, i)
        cand = _upsert_candidate(db, profile, display, is_complete=is_complete)
        upserted += 1
        result = db.execute(
            pg_insert(RoleCandidate)
            .values(
                role_id=role_id,
                candidate_id=cand.id,
                batch_id=batch.id,
                role_name=role.role_name or "",
                pulled_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["role_id", "candidate_id"])
        )
        rc = result.rowcount if result.rowcount is not None else -1
        if rc == 1:
            role_candidate_inserts += 1
        stored.append(cand)
        seen.add(url)

    logger.info(
        "pull_batch store step upserted_candidates=%s "
        "role_candidates_inserted=%s used_short_fallback=%s "
        "skipped_no_profile=%s stored_list=%s — about to commit",
        upserted,
        role_candidate_inserts,
        used_short_fallback,
        skipped_no_profile,
        len(stored),
    )

    role.last_page = page
    role.updated_at = datetime.now(timezone.utc)
    db.commit()
    logger.info(
        "pull_batch DB commit ok role_id=%s batch_id=%s last_page=%s",
        role_id,
        batch.id,
        page,
    )

    incomplete_n = sum(1 for c in stored if not c.is_complete_profile)
    summary = (
        f"Pulled {len(stored)} new (skipped {skipped_repeats} repeats "
        f"across {pages_scanned} pages)."
    )
    if incomplete_n:
        summary += (
            f" {incomplete_n} incomplete (thin Short profile — needs re-pull)."
        )
    logger.info(
        "returning %s candidates to UI batch_id=%s run_id=%s status=%s "
        "incomplete=%s summary=%s",
        len(stored),
        batch.id,
        run_id,
        status,
        incomplete_n,
        summary,
    )
    return {
        "candidates": [_candidate_row(c) for c in stored],
        "summary": summary,
        "batch_id": str(batch.id),
        "batch_number": batch_number,
        "pages_scanned": pages_scanned,
        "apify_run_id": run_id,
        "status": status,
        "incomplete_count": incomplete_n,
    }


def list_role_candidates(db: Session, role_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(Candidate, RoleCandidate.pulled_at, RoleCandidate.batch_id)
        .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
        .where(RoleCandidate.role_id == role_id)
        .order_by(RoleCandidate.pulled_at.desc())
    ).all()
    out = []
    for cand, pulled_at, batch_id in rows:
        row = _candidate_row(cand)
        row["pulled_at"] = pulled_at.isoformat() if pulled_at else None
        row["batch_id"] = str(batch_id) if batch_id else None
        out.append(row)
    return out


def count_incomplete_for_role(db: Session, role_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(Candidate)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(
                RoleCandidate.role_id == role_id,
                Candidate.is_complete_profile.is_(False),
            )
        ).scalar()
        or 0
    )


def list_incomplete_for_role(
    db: Session, role_id: uuid.UUID
) -> list[tuple[Candidate, RoleCandidate]]:
    return list(
        db.execute(
            select(Candidate, RoleCandidate)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(
                RoleCandidate.role_id == role_id,
                Candidate.is_complete_profile.is_(False),
            )
        ).all()
    )


def list_retryable_incomplete_for_role(
    db: Session, role_id: uuid.UUID
) -> list[tuple[Candidate, RoleCandidate]]:
    """Incomplete stubs still under the auto-retry cap."""
    return list(
        db.execute(
            select(Candidate, RoleCandidate)
            .join(RoleCandidate, RoleCandidate.candidate_id == Candidate.id)
            .where(
                RoleCandidate.role_id == role_id,
                Candidate.is_complete_profile.is_(False),
                Candidate.enrich_retry_count < MAX_ENRICH_RETRY_ATTEMPTS,
            )
        ).all()
    )


def retry_incomplete_profiles(db: Session, role_id: uuid.UUID) -> dict[str, Any]:
    """Re-attempt Full enrich for incomplete candidates under the retry cap."""
    role = db.get(Role, role_id)
    if not role:
        raise ValueError(f"role {role_id} not found")

    all_incomplete = list_incomplete_for_role(db, role_id)
    pairs = list_retryable_incomplete_for_role(db, role_id)
    exhausted_n = len(all_incomplete) - len(pairs)
    if not pairs:
        if exhausted_n:
            return {
                "upgraded": 0,
                "still_incomplete": exhausted_n,
                "exhausted": exhausted_n,
                "retryable": 0,
                "summary": (
                    f"No retryable profiles — {exhausted_n} failed after "
                    f"{MAX_ENRICH_RETRY_ATTEMPTS} attempts (manual re-pull)."
                ),
                "candidates": [_candidate_row(c) for c, _ in all_incomplete],
                "max_enrich_retry_attempts": MAX_ENRICH_RETRY_ATTEMPTS,
            }
        return {
            "upgraded": 0,
            "still_incomplete": 0,
            "exhausted": 0,
            "retryable": 0,
            "summary": "No incomplete profiles to retry.",
            "candidates": [],
            "max_enrich_retry_attempts": MAX_ENRICH_RETRY_ATTEMPTS,
        }

    incomplete = [cand for cand, _rc in pairs]
    short_by_url: dict[str, dict] = {}
    target_urls: list[str] = []
    for cand in incomplete:
        url = _normalize_url(cand.linkedin_url)
        if not url:
            continue
        target_urls.append(url)
        raw = cand.raw_profile if isinstance(cand.raw_profile, dict) else {}
        short_by_url[url] = raw

    retrieval = dict(role.retrieval or {})
    pool_cap = int(retrieval.get("pool_cap") or 25)
    compiled = compile_retrieval({"retrieval": retrieval}, pool_cap)
    mode = retrieval.get("profileScraperMode") or "Full"
    last_page = max(1, int(role.last_page or 1))

    # Same as original pull: don't assume stored retrieval filters work as-is.
    # Prefer persisted working filters; else probe_with_relax (drop searchQuery first).
    effective_input, dropped_keys, used_persisted = _resolve_retry_effective_input(
        role, compiled
    )
    full_input = _build_retry_full_input(
        effective_input,
        mode=mode,
        last_page=last_page,
        target_count=len(target_urls),
    )

    logger.info(
        "retry_incomplete role_id=%s slug=%s targets=%s used_persisted=%s "
        "dropped_keys=%s full_input=%s",
        role_id,
        role.slug,
        len(target_urls),
        used_persisted,
        dropped_keys,
        json.dumps(full_input, default=str),
    )

    try:
        profiles, status, run_id = fetch_profiles(full_input)
    except ApifyTransientError as e:
        logger.info(
            "retry_incomplete Apify transient status=%s run_id=%s",
            e.status,
            e.run_id,
        )
        return {
            "upgraded": 0,
            "still_incomplete": len(incomplete),
            "summary": SOURCE_SLOW_MESSAGE,
            "error": "apify_transient",
            "status": e.status,
            "apify_run_id": e.run_id,
            "candidates": [_candidate_row(c) for c in incomplete],
        }

    # Persisted filters may go stale — rediscover via probe_with_relax if empty.
    if not profiles and used_persisted:
        logger.info(
            "retry_incomplete persisted filters returned 0 profiles; "
            "rediscovering via probe_with_relax"
        )
        _pool_n, _preview, effective_input = probe_with_relax(
            dict(compiled), start_page=1
        )
        dropped_keys = _dropped_actor_keys(compiled, effective_input)
        _set_effective_actor_input(role, compiled, effective_input)
        full_input = _build_retry_full_input(
            effective_input,
            mode=mode,
            last_page=last_page,
            target_count=len(target_urls),
        )
        logger.info(
            "retry_incomplete after rediscover dropped=%s full_input=%s",
            dropped_keys,
            json.dumps(full_input, default=str),
        )
        try:
            profiles, status, run_id = fetch_profiles(full_input)
        except ApifyTransientError as e:
            logger.info(
                "retry_incomplete Apify transient after rediscover "
                "status=%s run_id=%s",
                e.status,
                e.run_id,
            )
            return {
                "upgraded": 0,
                "still_incomplete": len(incomplete),
                "summary": SOURCE_SLOW_MESSAGE,
                "error": "apify_transient",
                "status": e.status,
                "apify_run_id": e.run_id,
                "candidates": [_candidate_row(c) for c in incomplete],
            }

    by_url: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    by_stem: dict[str, dict] = {}
    _index_full_profiles(profiles, by_url, by_id, by_stem)

    unresolved = [
        u
        for u in target_urls
        if not _resolve_full_profile(
            u,
            by_url=by_url,
            by_id=by_id,
            short_by_url=short_by_url,
            by_stem=by_stem,
        )
    ]
    if unresolved:
        retry_status, retry_run_id = _retry_full_enrich_for_missing(
            full_input=full_input,
            missing_urls=unresolved,
            by_url=by_url,
            by_id=by_id,
            short_by_url=short_by_url,
            by_stem=by_stem,
        )
        if retry_run_id:
            run_id = retry_run_id
        if retry_status:
            status = retry_status

    batch_number = _next_batch_number(db, role_id)
    batch = _log_apify_call(
        db,
        role_id=role_id,
        batch_number=batch_number,
        apify_run_id=run_id,
        params_snapshot={
            "profileScraperMode": mode,
            "retry_incomplete": True,
            "target_count": len(target_urls),
            "actor_status": status,
            "full_enrich_raw_count": len(by_url),
            "dropped_keys": dropped_keys,
            "used_persisted_filters": used_persisted,
            "actor_input": full_input,
        },
    )

    upgraded: list[Candidate] = []
    still: list[Candidate] = []
    newly_exhausted = 0
    for i, cand in enumerate(incomplete):
        url = _normalize_url(cand.linkedin_url)
        profile = None
        if url:
            profile = _resolve_full_profile(
                url,
                by_url=by_url,
                by_id=by_id,
                short_by_url=short_by_url,
                by_stem=by_stem,
            )
        if not profile:
            cand.enrich_retry_count = _enrich_retry_count(cand) + 1
            if is_enrich_retry_exhausted(cand):
                newly_exhausted += 1
            still.append(cand)
            continue
        display = compact(profile, i)
        updated = _upsert_candidate(
            db, profile, display, is_complete=True, existing=cand
        )
        upgraded.append(updated)

    role.updated_at = datetime.now(timezone.utc)
    db.commit()

    exhausted_total = exhausted_n + newly_exhausted
    summary = (
        f"Retry incomplete: upgraded {len(upgraded)}, "
        f"still incomplete {len(still) + exhausted_n}"
    )
    if newly_exhausted:
        summary += (
            f" ({newly_exhausted} hit {MAX_ENRICH_RETRY_ATTEMPTS}-attempt cap)"
        )
    summary += "."
    if dropped_keys:
        summary += f" (relaxed filters: dropped {', '.join(dropped_keys)})"
    logger.info(
        "retry_incomplete done role_id=%s batch_id=%s upgraded=%s still=%s "
        "newly_exhausted=%s dropped_keys=%s",
        role_id,
        batch.id,
        len(upgraded),
        len(still),
        newly_exhausted,
        dropped_keys,
    )
    # Include already-exhausted stubs in the response so the UI can label them.
    exhausted_rows = [
        c for c, _ in all_incomplete if is_enrich_retry_exhausted(c) and c not in still
    ]
    return {
        "upgraded": len(upgraded),
        "still_incomplete": len(still) + exhausted_n,
        "exhausted": exhausted_total,
        "retryable": sum(1 for c in still if not is_enrich_retry_exhausted(c)),
        "summary": summary,
        "batch_id": str(batch.id),
        "apify_run_id": run_id,
        "status": status,
        "dropped_keys": dropped_keys,
        "max_enrich_retry_attempts": MAX_ENRICH_RETRY_ATTEMPTS,
        "candidates": [
            _candidate_row(c) for c in upgraded + still + exhausted_rows
        ],
    }
