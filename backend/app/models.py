"""SQLAlchemy models matching migrations/001_init.sql (+ 002–008)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    role_name: Mapped[str] = mapped_column(Text, nullable=False)
    client: Mapped[Optional[str]] = mapped_column(Text)
    retrieval: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Filters that actually returned hits after probe_with_relax
    # ({"dropped_keys": [...], "actor_input": {...}}). Used by retry-incomplete.
    effective_actor_input: Mapped[Optional[dict]] = mapped_column(JSONB)
    last_page: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jd_text: Mapped[Optional[str]] = mapped_column(Text)
    # Structured JobRoleSchema JSON — when set, scoring sends parsed_jd (no Claude).
    jd_parsed: Mapped[Optional[dict]] = mapped_column(JSONB)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    linkedin_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    headline: Mapped[Optional[str]] = mapped_column(Text)
    current_title: Mapped[Optional[str]] = mapped_column(Text)
    current_company: Mapped[Optional[str]] = mapped_column(Text)
    location: Mapped[Optional[str]] = mapped_column(Text)
    top_skills: Mapped[Optional[str]] = mapped_column(Text)
    raw_profile: Mapped[Optional[dict]] = mapped_column(JSONB)
    # False when raw_profile is a Short stub (no experience/skills/about) stored
    # after Full enrich failed — not equivalent to a Full profile for ML/UI.
    is_complete_profile: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PullBatch(Base):
    __tablename__ = "pull_batches"
    __table_args__ = (UniqueConstraint("role_id", "batch_number", name="pull_batches_role_batch_uidx"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"))
    batch_number: Mapped[int] = mapped_column(Integer, nullable=False)
    apify_run_id: Mapped[Optional[str]] = mapped_column(Text)
    params_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleCandidate(Base):
    __tablename__ = "role_candidates"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_batches.id", ondelete="SET NULL")
    )
    # Snapshot of roles.role_name at search/pull time — readability only, not for lookups.
    role_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total_score: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    component_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB)
    matched_signals: Mapped[Optional[list]] = mapped_column(ARRAY(Text))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # "parsed" | "llm" — which /score mode produced this score (audit trail).
    scoring_mode: Mapped[Optional[str]] = mapped_column(Text)
    # LLM narratives (summary + assessment), cached by narrative_jd_hash.
    summary_text: Mapped[Optional[str]] = mapped_column(Text)
    assessment_text: Mapped[Optional[str]] = mapped_column(Text)
    narrative_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    narrative_jd_hash: Mapped[Optional[str]] = mapped_column(Text)
    # reviewing | shortlisted | benched — default reviewing so newly scored
    # candidates land in the Review queue automatically.
    review_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="reviewing", server_default="reviewing"
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="intake")
    intake_progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[List["ChatMessage"]] = relationship(
        back_populates="session", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
