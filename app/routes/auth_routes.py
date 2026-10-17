"""Accounts (WTA-26): register, log in, and who am I.

Register and login both answer with a token, so a client that just signed up is logged in
without a second request. Login never says which of the two fields was wrong.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from config.variables import settings
from db import db_crud
from db.conn import get_db
from db.models_db.models_db import User
from models.models import Token, UserCredentials, UserRead
from security.auth import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: User) -> Token:
    return Token(
        access_token=create_access_token(user.id),
        expires_in=settings.auth_token_expire_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=Token,
    summary="Create an account and log in",
    responses={409: {"description": "An account with this email already exists"}},
)
def register(payload: UserCredentials, db: Session = Depends(get_db)):
    if db_crud.get_user_by_email(db, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists. Log in instead.",
        )
    user = db_crud.create_user(db, email=payload.email, password_hash=hash_password(payload.password))
    return _token_for(user)


@router.post(
    "/login",
    response_model=Token,
    summary="Log in with email and password",
    responses={401: {"description": "Wrong email or password"}},
)
def login(payload: UserCredentials, db: Session = Depends(get_db)):
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
