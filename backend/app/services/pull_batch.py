"""pull_batch — Short probe → dedupe → Full enrich only new URLs → store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.apify.client import (
    compact,
    compile_retrieval,
    fetch_profiles_by_urls,
    probe_pool,
    probe_with_relax,
)
from app.models import Candidate, PullBatch, Role, RoleCandidate


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


def _upsert_candidate(db: Session, profile: dict, shaped: dict) -> Candidate:
    url = _normalize_url(shaped.get("linkedinUrl"))
    if not url:
        raise ValueError("profile missing linkedinUrl")

    existing = db.execute(
        select(Candidate).where(Candidate.linkedin_url == url)
    ).scalar_one_or_none()

    if existing:
        existing.first_name = shaped.get("firstName") or existing.first_name
        existing.last_name = shaped.get("lastName") or existing.last_name
        existing.headline = shaped.get("headline") or existing.headline
        existing.current_title = shaped.get("current_title") or existing.current_title
        existing.current_company = shaped.get("current_company") or existing.current_company
        existing.location = shaped.get("location") or existing.location
        existing.top_skills = shaped.get("topSkills") or existing.top_skills
        existing.raw_profile = profile
        db.flush()
        return existing

    cand = Candidate(
        id=uuid.uuid4(),
        linkedin_url=url,
        first_name=shaped.get("firstName"),
        last_name=shaped.get("lastName"),
        headline=shaped.get("headline"),
        current_title=shaped.get("current_title"),
        current_company=shaped.get("current_company"),
        location=shaped.get("location"),
        top_skills=shaped.get("topSkills"),
        raw_profile=profile,
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

    actor_input = compile_retrieval({"retrieval": retrieval}, pool_cap)
    page = int(role.last_page or 0) + 1
    new_urls: list[str] = []
    pages_scanned = 0
    skipped_repeats = 0
    seen = _seen_urls_for_role(db, role_id)
    effective_input = dict(actor_input)
    first_page = True
    max_pages = 40

    while len(new_urls) < batch_size and pages_scanned < max_pages:
        if first_page:
            _pool_n, preview, effective_input = probe_with_relax(
                effective_input, start_page=page
            )
            first_page = False
        else:
            _pool_n, preview = probe_pool(effective_input, start_page=page)

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
            },
        )
        pages_scanned += 1

        if not preview:
            break

        for item in preview:
            url = _normalize_url(item.get("linkedinUrl"))
            if not url:
                continue
            if url in seen or url in new_urls:
                skipped_repeats += 1
                continue
            new_urls.append(url)
            if len(new_urls) >= batch_size:
                break

        if len(new_urls) >= batch_size:
            break
        page += 1

    target_urls = [u for u in new_urls[:batch_size] if u not in seen]

    if not target_urls:
        role.last_page = page
        role.updated_at = datetime.now(timezone.utc)
        db.commit()
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
    profiles, status, run_id = fetch_profiles_by_urls(target_urls, mode=mode)

    batch_number = _next_batch_number(db, role_id)
    batch = _log_apify_call(
        db,
        role_id=role_id,
        batch_number=batch_number,
        apify_run_id=run_id,
        params_snapshot={
            "profileScraperMode": mode,
            "page": page,
            "profileUrls": target_urls,
            "maxItems": len(target_urls),
            "retrieval": retrieval,
            "actor_status": status,
            "probe_pages": pages_scanned,
        },
    )

    by_url: dict[str, dict] = {}
    for p in profiles:
        u = _normalize_url(p.get("linkedinUrl"))
        if u:
            by_url[u] = p

    stored: list[Candidate] = []
    for i, url in enumerate(target_urls):
        profile = by_url.get(url)
        if not profile:
            continue
        shaped = compact(profile, i)
        cand = _upsert_candidate(db, profile, shaped)
        db.execute(
            pg_insert(RoleCandidate)
            .values(
                role_id=role_id,
                candidate_id=cand.id,
                batch_id=batch.id,
                pulled_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["role_id", "candidate_id"])
        )
        stored.append(cand)
        seen.add(url)

    role.last_page = page
    role.updated_at = datetime.now(timezone.utc)
    db.commit()

    summary = (
        f"Pulled {len(stored)} new (skipped {skipped_repeats} repeats "
        f"across {pages_scanned} pages)."
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
