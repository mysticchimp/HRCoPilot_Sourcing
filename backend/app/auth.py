"""Dummy login — env credentials only. No user DB."""

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


def authenticate_dummy(email: str, password: str) -> Optional[CurrentUser]:
    """Match against env-configured dummy accounts. Nothing is stored."""
    settings = get_settings()
    email_l = email.strip().lower()
    for account in settings.dummy_accounts:
        if email_l == account["email"] and password == account["password"]:
            return CurrentUser(email=account["email"], role=account["role"])
    return None


def create_access_token(email: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {"email": email, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_hours * 3600,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
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
    role = payload.get("role")
    if not email or role not in ("admin", "hr_manager"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )
    return CurrentUser(email=email, role=role)


def user_to_out(user: CurrentUser) -> UserOut:
    return UserOut(email=user.email, role=user.role)
