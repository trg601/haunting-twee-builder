from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
ACCESS_REFRESH_TOKEN_EXPIRE_DAYS = 30
if not SECRET_KEY or not ALGORITHM:
    raise ValueError("AUTH_SECRET and ALGORITHM must be set in environment variables")

GDRIVE_CHANNEL_TOKEN = environ.get("GDRIVE_CHANNEL_TOKEN", "")
if not GDRIVE_CHANNEL_TOKEN:
    raise ValueError("GDRIVE_CHANNEL_TOKEN must be set in environment variables")


class Token(BaseModel):
    access_token: str
    refresh_token: str
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


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> tuple[str, datetime]:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(days=ACCESS_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt, expire


def hash_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


async def issue_token_pair(db_pool: asyncpg.Pool, username: str) -> Token:
    token_data = {"sub": username}
    access_token = create_access_token(data=token_data)
    refresh_token, expires_at = create_refresh_token(data=token_data)
    await db_pool.execute(
        "insert into refresh_tokens (token_hash, username, expires_at) values ($1, $2, $3)",
        hash_token(refresh_token),
        username,
        expires_at,
    )
    return Token(
        access_token=access_token, refresh_token=refresh_token, token_type="bearer"
    )


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
    return await issue_token_pair(db_pool, user.username)


@auth_router.post("/refresh")
async def refresh_token_for_access_token(
    refresh_token: str, db_pool: PGPoolDep
) -> Token:
    try:
        payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError:
        raise CREDENTIALS_EXCEPTION
    username = payload.get("sub")
    if not username or payload.get("type") != "refresh":
        raise CREDENTIALS_EXCEPTION

    # Delete refresh token and create new one
    cmd = """
        delete from refresh_tokens
        where token_hash = $1 and username = $2
        and expires_at > now()
        returning username
    """
    row = await db_pool.fetchrow(
        cmd,
        hash_token(refresh_token),
        username,
    )

    if not row or not await get_user(db_pool, username):
        raise CREDENTIALS_EXCEPTION

    return await issue_token_pair(db_pool, username)
