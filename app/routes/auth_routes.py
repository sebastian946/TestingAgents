"""Accounts (WTA-26 / WTA-28): register, log in, who am I, email verification, password reset.

Register and login both answer with a token, so a client that just signed up is logged in
without a second request. Login never says which of the two fields was wrong, and the
forgot-password endpoint answers the same whether the email exists or not.

Emails go out in background tasks (`services/mailer.py`): a provider outage is logged, it
never turns a registration into a 500. The user can ask for a new link.
"""
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import User
from models.models import EmailRequest, PasswordConfirm, PasswordReset, Token, TokenRequest, UserCredentials, UserRead
from security import ratelimit
from security.auth import create_access_token, get_current_user, hash_password, verify_password
from security.passwords import PWNED_MESSAGE, is_pwned
from services import cleanup, mailer

router = APIRouter(prefix="/auth", tags=["auth"])

VERIFY, RESET = "verify", "reset"


def _token_for(user: User) -> Token:
    return Token(
        access_token=create_access_token(user),
        expires_in=settings.auth_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


def _deliver(email: mailer.Email) -> None:
    try:
        mailer.send_email(email)
    except Exception as exc:  # noqa: BLE001  background task: log, never crash the request
        print(f"[mail] FAILED to={email.to} subject={email.subject!r}: {exc}", flush=True)


def _queue_verification(db: Session, user: User, tasks: BackgroundTasks) -> None:
    token = db_crud.create_email_token(db, user.id, VERIFY, timedelta(hours=settings.auth_verify_token_hours))
    tasks.add_task(_deliver, mailer.verification_email(user.email, token))


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=Token,
    summary="Create an account, send the verification email and log in",
    responses={409: {"description": "An account with this email already exists"}},
)
def register(payload: UserCredentials, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    ratelimit.limiter.enforce(ratelimit.REGISTER_IP, ratelimit.client_ip(request))
    if is_pwned(payload.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=PWNED_MESSAGE)
    if db_crud.get_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists. Log in instead.",
        )
    user = db_crud.create_user(db, email=payload.email, password_hash=hash_password(payload.password))
    _queue_verification(db, user, tasks)
    return _token_for(user)


@router.post(
    "/login",
    response_model=Token,
    summary="Log in with email and password",
    responses={401: {"description": "Wrong email or password"}},
)
def login(payload: UserCredentials, request: Request, db: Session = Depends(get_db)):
    # Per IP and per account (WTA-30): a password cannot be guessed by brute force, from one
    # machine or from many
    ratelimit.limiter.enforce(ratelimit.LOGIN_IP, ratelimit.client_ip(request))
    ratelimit.limiter.enforce(ratelimit.LOGIN_EMAIL, payload.email)
    user = db_crud.get_user_by_email(db, payload.email)
    # verify_password runs even when the user is unknown, so timing does not reveal emails
    if not verify_password(payload.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong email or password.")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This account is disabled.")
    return _token_for(user)


@router.get("/me", response_model=UserRead, summary="The account behind the token")
def me(user: User = Depends(get_current_user)):
    return user


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the account with all its jobs, pages, scenarios and reports",
    responses={403: {"description": "Wrong password"}},
)
def delete_me(payload: PasswordConfirm, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Irreversible (WTA-30). The password is asked again so a stolen token alone cannot
    wipe an account. The subscription row goes too; cancelling the payment itself is done
    by the user at the provider's portal (we never hold their card)."""
    # 403, not 401: a 401 would make the dashboard treat the session as expired
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Wrong password.")
    removed = cleanup.delete_account(db, settings.reports_dir, user.id)
    print(f"[auth] account {user.id} deleted with {removed} job(s)", flush=True)


# ------------------------------------------------------------ email verification

@router.post(
    "/verify/request",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the verification email again",
)
def request_verification(tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.email_verified_at is not None:
        return {"detail": "This email is already verified."}
    ratelimit.limiter.enforce(ratelimit.VERIFY_REQUEST_USER, str(user.id))
    _queue_verification(db, user, tasks)
    return {"detail": f"Verification email sent to {user.email}."}


@router.post(
    "/verify",
    response_model=UserRead,
    summary="Confirm the email with the token from the link",
    responses={400: {"description": "Unknown, used or expired token"}},
)
def verify_email(payload: TokenRequest, request: Request, db: Session = Depends(get_db)):
    ratelimit.limiter.enforce(ratelimit.TOKEN_IP, ratelimit.client_ip(request))
    user = db_crud.consume_email_token(db, payload.token, VERIFY)
    if user is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired. Log in and request a new one.",
        )
    return db_crud.mark_email_verified(db, user)  # commits the token and the timestamp together


# --------------------------------------------------------------- password reset

@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a password-reset link (same answer whether the email exists or not)",
)
def forgot_password(payload: EmailRequest, request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    ratelimit.limiter.enforce(ratelimit.FORGOT_IP, ratelimit.client_ip(request))
    ratelimit.limiter.enforce(ratelimit.FORGOT_EMAIL, payload.email)
    user = db_crud.get_user_by_email(db, payload.email)
    if user is not None and user.is_active:
        token = db_crud.create_email_token(db, user.id, RESET, timedelta(minutes=settings.auth_reset_token_minutes))
        tasks.add_task(_deliver, mailer.password_reset_email(user.email, token))
    return {"detail": "If that email has an account, a reset link is on its way."}


@router.post(
    "/password/reset",
    response_model=Token,
    summary="Set a new password with the token from the link; logs in and logs out every other session",
    responses={400: {"description": "Unknown, used or expired token"}},
)
def reset_password(payload: PasswordReset, request: Request, db: Session = Depends(get_db)):
    ratelimit.limiter.enforce(ratelimit.TOKEN_IP, ratelimit.client_ip(request))
    if is_pwned(payload.password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=PWNED_MESSAGE)
    user = db_crud.consume_email_token(db, payload.token, RESET)
    if user is None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired. Request a new one.",
        )
    # Someone who can read the email owns the address: that counts as verifying it
    user.email_verified_at = user.email_verified_at or db_crud._now()
    user = db_crud.set_password(db, user, hash_password(payload.password))  # commits; bumps token_version
    return _token_for(user)
