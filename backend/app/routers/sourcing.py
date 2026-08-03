from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Role
from app.apify.client import SOURCE_SLOW_MESSAGE, ApifyTransientError
from app.services import chat as chat_service
from app.services.pull_batch import list_role_candidates, pull_batch

router = APIRouter(tags=["sourcing"])


class MessageIn(BaseModel):
    message: str = Field(min_length=1)
    session_id: Optional[str] = None


class PullIn(BaseModel):
    batch_size: Optional[int] = Field(default=None, ge=10, le=150)


class RoleOut(BaseModel):
    id: str
    slug: str
    role_name: str
    client: Optional[str]
    last_page: int
    pool_cap: Optional[int] = None


@router.get("/health")
def health():
    return {"ok": True, "service": "contra6-sourcing"}


@router.get("/roles", response_model=List[RoleOut])
def list_roles(db: Session = Depends(get_db)):
    rows = db.execute(select(Role).order_by(Role.updated_at.desc())).scalars().all()
    return [
        RoleOut(
            id=str(r.id),
            slug=r.slug,
            role_name=r.role_name,
            client=r.client,
            last_page=r.last_page,
            pool_cap=(r.retrieval or {}).get("pool_cap"),
        )
        for r in rows
    ]


@router.post("/roles/{slug}/session")
def start_or_resume_session(slug: str, db: Session = Depends(get_db)):
    """Start a chat session for an existing role, or intake for slug=new."""
    try:
        return chat_service.start_session(db, None if slug == "new" else slug)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/chat/{role_slug}/message")
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


@router.get("/roles/{slug}/candidates")
def get_candidates(slug: str, db: Session = Depends(get_db)):
    role = db.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
    return {"role": role.slug, "candidates": list_role_candidates(db, role.id)}


@router.post("/roles/{slug}/pull")
def pull_role_batch(
    slug: str, body: Optional[PullIn] = None, db: Session = Depends(get_db)
):
    """Direct pull endpoint (also invoked from the chat confirm/ready flows)."""
    role = db.execute(select(Role).where(Role.slug == slug)).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="role not found")
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
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Something went wrong on our side — please try again.",
        ) from None
