"""pull_batch — Short probe → dedupe → Full enrich only new URLs → store."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
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


def _upsert_candidate(db: Session, raw_apify_item: dict, display: dict) -> Candidate:
    """Persist one candidate.

    raw_apify_item — untouched Apify dataset item (Full mode) → raw_profile JSONB.
    display — compact() output used ONLY for flattened UI columns.
    """
    url = _normalize_url(display.get("linkedinUrl")) or _normalize_url(
        raw_apify_item.get("linkedinUrl")
    )
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
        "_upsert_candidate url=%s raw_keys=%s about_chars=%s experience_len=%s "
        "education_len=%s skills_len=%s",
        url,
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

    existing = db.execute(
        select(Candidate).where(Candidate.linkedin_url == url)
    ).scalar_one_or_none()

    if existing:
        existing.first_name = display.get("firstName") or existing.first_name
        existing.last_name = display.get("lastName") or existing.last_name
        existing.headline = display.get("headline") or existing.headline
        existing.current_title = display.get("current_title") or existing.current_title
        existing.current_company = (
            display.get("current_company") or existing.current_company
        )
        existing.location = display.get("location") or existing.location
        existing.top_skills = display.get("topSkills") or existing.top_skills
        existing.raw_profile = raw_apify_item
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
        first_seen_at=datetime.now(timezone.utc),
    )
    db.add(cand)
    db.flush()
    return cand


def _candidate_row(c: Candidate) -> dict:
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
            "full_enrich_raw_count": len(profiles) if profiles else 0,
            "actor_input": full_input,
        },
    )

    by_url: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    full_missing_url = 0
    for p in profiles:
        u = _normalize_url(p.get("linkedinUrl"))
        if u:
            by_url[u] = p
        else:
            full_missing_url += 1
        pid = p.get("id") or p.get("profileIdInSearch")
        if pid is not None:
            by_id[str(pid)] = p
    logger.info(
        "pull_batch Full search URL index size=%s id_index=%s "
        "missing_linkedinUrl=%s target_urls=%s overlap_by_url=%s",
        len(by_url),
        len(by_id),
        full_missing_url,
        len(target_urls),
        len(set(by_url) & set(target_urls)),
    )

    stored: list[Candidate] = []
    upserted = 0
    role_candidate_inserts = 0
    used_short_fallback = 0
    skipped_no_profile = 0
    for i, url in enumerate(target_urls):
        # Prefer Full search item (has about/experience). Match by URL, then by
        # Short-item id if LinkedIn URL forms differ across modes.
        profile = by_url.get(url)
        source = "full"
        if not profile:
            short = short_by_url.get(url) or {}
            sid = short.get("id") or short.get("profileIdInSearch")
            if sid is not None:
                profile = by_id.get(str(sid))
        if not profile:
            # Last resort: Short hit — incomplete for ML (no about/experience).
            profile = short_by_url.get(url)
            source = "short_fallback"
        if not profile:
            skipped_no_profile += 1
            logger.info(
                "pull_batch no Full or Short profile for target url=%s",
                url,
            )
            continue
        if source == "short_fallback":
            used_short_fallback += 1
            logger.warning(
                "pull_batch storing Short fallback as raw_profile (no about/"
                "experience) url=%s — Full search miss",
                url,
            )

        display = compact(profile, i)
        # raw_profile = untouched Apify item; display columns from compact() only.
        cand = _upsert_candidate(db, profile, display)
        upserted += 1
        result = db.execute(
            pg_insert(RoleCandidate)
            .values(
                role_id=role_id,
                candidate_id=cand.id,
                batch_id=batch.id,
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

    summary = (
        f"Pulled {len(stored)} new (skipped {skipped_repeats} repeats "
        f"across {pages_scanned} pages)."
    )
    logger.info(
        "returning %s candidates to UI batch_id=%s run_id=%s status=%s summary=%s",
        len(stored),
        batch.id,
        run_id,
        status,
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
