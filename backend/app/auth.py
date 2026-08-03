"""Dummy login — any non-empty email/password works. Nothing stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Cookie, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.config import get_settings

COOKIE_NAME = "access_token"
ALGORITHM = "HS256"


class LoginIn(BaseModel):
    email: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    email: str
    role: str


@dataclass(frozen=True)
class CurrentUser:
    email: str
    role: str


def create_access_token(email: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"email": email, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    # Cross-site (Vercel UI → Render API) needs SameSite=None + Secure.
    secure = settings.cookie_secure
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="none" if secure else "lax",
        secure=secure,
        max_age=settings.jwt_expire_hours * 3600,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    settings = get_settings()
    secure = settings.cookie_secure
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="none" if secure else "lax",
        secure=secure,
    )


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        ) from e


def get_current_user(
    access_token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME),
) -> CurrentUser:
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload = decode_token(access_token)
    email = payload.get("email")
    role = payload.get("role") or "admin"
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    return CurrentUser(email=str(email), role=str(role))


def user_to_out(user: CurrentUser) -> UserOut:
    return UserOut(email=user.email, role=user.role)
