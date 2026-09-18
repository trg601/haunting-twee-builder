from contextlib import asynccontextmanager
from os import environ
from typing import Annotated

import asyncpg
from fastapi import Depends, FastAPI, Request

from tweebuilder.gcp_service import GCPService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Created once per app lifetime and reused across all requests.
    app.state.gcp_service = GCPService()
    app.state.db_pool = await asyncpg.create_pool(dsn=environ["DATABASE_URL"])
    yield
    await app.state.db_pool.close()


def get_gcp_service(request: Request) -> GCPService:
    return request.app.state.gcp_service


def get_db_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


GCPServiceDep = Annotated[GCPService, Depends(get_gcp_service)]
PGPoolDep = Annotated[asyncpg.Pool, Depends(get_db_pool)]
