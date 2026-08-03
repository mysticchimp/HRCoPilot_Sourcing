"""Login / logout / me — accept any credentials, issue a session cookie."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.auth import (
    CurrentUser,
    LoginIn,
    UserOut,
    clear_auth_cookie,
    create_access_token,
    get_current_user,
    set_auth_cookie,
    user_to_out,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response):
    # Truly dummy: any non-empty email/password is fine.
    email = body.email.strip() or "guest"
    user = CurrentUser(email=email, role="admin")
    token = create_access_token(user.email, user.role)
    set_auth_cookie(response, token)
    return user_to_out(user)


@router.post("/logout")
def logout(response: Response):
    clear_auth_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)):
    return user_to_out(user)
