from datetime import UTC, datetime, timedelta
from os import environ
from secrets import compare_digest
from typing import Annotated

import asyncpg
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pydantic import BaseModel

from tweebuilder.app_context import PGPoolDep

SECRET_KEY = environ.get("AUTH_SECRET", "")
ALGORITHM = environ.get("AUTH_ALGORITHM", "")
ACCESS_TOKEN_EXPIRE_MINUTES = 30
if not SECRET_KEY or not ALGORITHM:
    raise ValueError("AUTH_SECRET and ALGORITHM must be set in environment variables")

GDRIVE_CHANNEL_TOKEN = environ.get("GDRIVE_CHANNEL_TOKEN", "")
if not GDRIVE_CHANNEL_TOKEN:
    raise ValueError("GDRIVE_CHANNEL_TOKEN must be set in environment variables")


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: str | None = None


class User(BaseModel):
    username: str


class UserWithHash(User):
    hashed_password: str


password_hash = PasswordHash.recommended()

DUMMY_HASH = password_hash.hash("dummypassword")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

auth_router = APIRouter()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return password_hash.hash(password)


async def get_user(db_pool: asyncpg.Pool, username: str) -> UserWithHash | None:
    row = await db_pool.fetchrow(
        "select username, hashed_password from users where username = $1",
        username,
    )
    return UserWithHash(**row) if row else None


async def authenticate_user(
    db_pool: asyncpg.Pool, username: str, password: str
) -> User | None:
    user = await get_user(db_pool, username)
    if not user:
        # Use dummy password hash as recommended by FastAPI docs to avoid timing attacks
        verify_password(password, DUMMY_HASH)
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authorized",
    headers={"WWW-Authenticate": "Bearer"},
)


async def authenticate_user_from_token(
    db_pool: PGPoolDep, token: Annotated[str, Depends(oauth2_scheme)]
) -> User:
    if environ.get("ENVIRONMENT") == "development":
        return User(username="ty")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError:
        raise CREDENTIALS_EXCEPTION
    username = payload.get("sub")
    if not username:
        raise CREDENTIALS_EXCEPTION
    user = await get_user(db_pool, username)
    if not user:
        raise CREDENTIALS_EXCEPTION
    return user


# Add this to any route's parameters to require a valid bearer token, e.g. `user: CurrentUserDep`
AuthenticateUserDep = Annotated[User, Depends(authenticate_user_from_token)]


async def verify_gdrive_channel_token(
    x_goog_channel_token: Annotated[str | None, Header()] = None,
) -> None:
    if not x_goog_channel_token or not compare_digest(
        x_goog_channel_token, GDRIVE_CHANNEL_TOKEN
    ):
        raise CREDENTIALS_EXCEPTION
    try:
        jwt.decode(x_goog_channel_token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError:
        raise CREDENTIALS_EXCEPTION


GDriveChannelTokenDep = Annotated[None, Depends(verify_gdrive_channel_token)]


@auth_router.post("/token")
async def login_for_access_token(
    db_pool: PGPoolDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    user = await authenticate_user(db_pool, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")
