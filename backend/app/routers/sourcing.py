from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.apify.client import SOURCE_SLOW_MESSAGE, ApifyTransientError
from app.auth import get_current_user
from app.db import get_db
from app.models import Role
from app.services import chat as chat_service
from app.services import narrative as narrative_service
from app.services import review as review_service
from app.services import scoring as scoring_service
from app.services.pull_batch import list_role_candidates, pull_batch, retry_incomplete_profiles
from app.services.scoring import ScoringTransientError

router = APIRouter(tags=["sourcing"])
protected = APIRouter(dependencies=[Depends(get_current_user)])


class MessageIn(BaseModel):
    message: str = Field(min_length=1)
    session_id: Optional[str] = None


class PullIn(BaseModel):
    batch_size: Optional[int] = Field(default=None, ge=10, le=150)


class JdIn(BaseModel):
    jd_text: str = Field(min_length=1)


class ReviewStatusIn(BaseModel):
    status: Literal["reviewing", "shortlisted", "benched"]


class RoleOut(BaseModel):
    id: str
    slug: str
    role_name: str
    client: Optional[str]
    last_page: int
    pool_cap: Optional[int] = None
    archived_at: Optional[str] = None
    has_jd: bool = False


def _role_out(r: Role) -> RoleOut:
    return RoleOut(
        id=str(r.id),
        slug=r.slug,
        role_name=r.role_name,
        client=r.client,
        last_page=r.last_page,
        pool_cap=(r.retrieval or {}).get("pool_cap"),
        archived_at=r.archived_at.isoformat() if r.archived_at else None,
        has_jd=scoring_service.role_has_jd(r),
    )


def _get_role_by_slug(db: Session, slug: str) -> Role:
    role = db.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    return role


@router.get("/health")
def health():
    return {"ok": True, "service": "contra6-sourcing"}


@protected.get("/roles", response_model=List[RoleOut])
def list_roles(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    stmt = select(Role).order_by(Role.updated_at.desc())
    if not include_archived:
        stmt = stmt.where(Role.archived_at.is_(None))
    rows = db.execute(stmt).scalars().all()
    return [_role_out(r) for r in rows]


@protected.get("/roles/archived", response_model=List[RoleOut])
def list_archived_roles(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Role)
        .where(Role.archived_at.is_not(None))
        .order_by(Role.archived_at.desc())
    ).scalars().all()
    return [_role_out(r) for r in rows]


@protected.post("/roles/{slug}/archive", response_model=RoleOut)
def archive_role(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    if role.archived_at is None:
        role.archived_at = datetime.now(timezone.utc)
        role.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(role)
    return _role_out(role)


@protected.post("/roles/{slug}/unarchive", response_model=RoleOut)
def unarchive_role(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    if role.archived_at is not None:
        role.archived_at = None
        role.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(role)
    return _role_out(role)


@protected.post("/roles/{slug}/session")
def start_or_resume_session(slug: str, db: Session = Depends(get_db)):
    """Start a chat session for an existing role, or intake for slug=new."""
    try:
        return chat_service.start_session(db, None if slug == "new" else slug)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@protected.post("/chat/{role_slug}/message")
def chat_message(role_slug: str, body: MessageIn, db: Session = Depends(get_db)):
    try:
        return chat_service.handle_message(
            db, role_slug, body.message, session_id=body.session_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ApifyTransientError:
        # Should already be caught inside pull_batch; belt-and-suspenders.
        raise HTTPException(status_code=503, detail=SOURCE_SLOW_MESSAGE) from None
    except Exception:
        # Never leak raw requests/urllib3 strings to the client.
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None


@protected.get("/roles/{slug}/candidates")
def get_candidates(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    return {"role": role.slug, "candidates": list_role_candidates(db, role.id)}


@protected.post("/roles/{slug}/jd")
def save_role_jd(slug: str, body: JdIn, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    try:
        role = scoring_service.save_role_jd(db, role, body.jd_text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": True,
        "role": role.slug,
        "jd_text": role.jd_text,
        "has_jd": scoring_service.role_has_jd(role),
        "has_parsed_jd": bool(role.jd_parsed),
        "jd_mode": "parsed" if role.jd_parsed else "text",
    }


@protected.post("/roles/{slug}/score")
def score_role(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    try:
        result = scoring_service.score_role(db, role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ScoringTransientError as e:
        raise HTTPException(status_code=503, detail=e.message) from None
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None
    ranked = result.get("ranked") or result.get("candidates") or []
    return {
        "role": role.slug,
        "jd_text": role.jd_text,
        "has_jd": scoring_service.role_has_jd(role),
        "has_parsed_jd": bool(role.jd_parsed),
        "jd_mode": "parsed" if role.jd_parsed else "text",
        "scoring_mode": result.get("scoring_mode"),
        "ranked": ranked,
        "not_yet_scored": result.get("not_yet_scored") or [],
        "not_yet_scored_count": result.get("not_yet_scored_count")
        or len(result.get("not_yet_scored") or []),
        "candidates": ranked,
        "count": result.get("count") or len(ranked),
        "skipped_incomplete": result.get("skipped_incomplete") or 0,
        "incomplete_candidates": result.get("incomplete_candidates") or [],
        "summary": result.get("summary"),
    }


@protected.get("/roles/{slug}/scores")
def get_scores(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    result = scoring_service.list_score_payload(db, role.id)
    ranked = result.get("ranked") or result.get("candidates") or []
    return {
        "role": role.slug,
        "jd_text": role.jd_text,
        "has_jd": scoring_service.role_has_jd(role),
        "has_parsed_jd": bool(role.jd_parsed),
        "jd_mode": "parsed" if role.jd_parsed else "text",
        "scoring_mode": result.get("scoring_mode"),
        "ranked": ranked,
        "not_yet_scored": result.get("not_yet_scored") or [],
        "not_yet_scored_count": result.get("not_yet_scored_count")
        or len(result.get("not_yet_scored") or []),
        "candidates": ranked,
        "count": result["count"],
        "skipped_incomplete": result["skipped_incomplete"],
        "incomplete_candidates": result["incomplete_candidates"],
    }


@protected.post("/roles/{slug}/narrate")
def narrate_role(slug: str, db: Session = Depends(get_db)):
    role = _get_role_by_slug(db, slug)
    try:
        result = narrative_service.narrate_role(db, role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ScoringTransientError as e:
        raise HTTPException(status_code=503, detail=e.message) from None
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None
    return {
        "role": role.slug,
        "generated": result.get("generated") or 0,
        "skipped_already_current": result.get("skipped_already_current") or 0,
        "failed": result.get("failed") or 0,
        "jd_hash": result.get("jd_hash"),
        "summary": result.get("summary"),
    }


@protected.get("/roles/{slug}/review-queue")
def get_review_queue(
    slug: str,
    status: Literal["reviewing", "shortlisted", "benched"] = Query(default="reviewing"),
    db: Session = Depends(get_db),
):
    role = _get_role_by_slug(db, slug)
    try:
        result = review_service.list_review_queue(db, role.id, status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "role": role.slug,
        "status": result["status"],
        "counts": result["counts"],
        "candidates": result["candidates"],
        "count": result["count"],
    }


@protected.post("/roles/{slug}/candidates/{candidate_id}/review-status")
def update_review_status(
    slug: str,
    candidate_id: UUID,
    body: ReviewStatusIn,
    db: Session = Depends(get_db),
):
    role = _get_role_by_slug(db, slug)
    try:
        result = review_service.set_review_status(
            db, role.id, candidate_id, body.status
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "role": role.slug,
        **result,
    }


@protected.post("/roles/{slug}/pull")
def pull_role_batch(
    slug: str, body: Optional[PullIn] = None, db: Session = Depends(get_db)
):
    """Direct pull endpoint (also invoked from the chat confirm/ready flows)."""
    role = _get_role_by_slug(db, slug)
    size = (body.batch_size if body and body.batch_size else None) or int(
        (role.retrieval or {}).get("pool_cap") or 25
    )
    try:
        return pull_batch(db, role.id, batch_size=size)
    except ApifyTransientError:
        return {
            "candidates": [],
            "summary": SOURCE_SLOW_MESSAGE,
            "batch_id": None,
            "pages_scanned": 0,
            "error": "apify_transient",
            "incomplete_count": 0,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None


@protected.post("/roles/{slug}/retry-incomplete")
def retry_incomplete(slug: str, db: Session = Depends(get_db)):
    """Re-attempt Full enrichment for incomplete (Short stub) candidates."""
    role = _get_role_by_slug(db, slug)
    try:
        return retry_incomplete_profiles(db, role.id)
    except ApifyTransientError:
        return {
            "upgraded": 0,
            "still_incomplete": 0,
            "summary": SOURCE_SLOW_MESSAGE,
            "error": "apify_transient",
            "candidates": [],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None


router.include_router(protected)
