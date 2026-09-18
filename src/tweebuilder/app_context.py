import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from os import environ
from typing import Annotated

import aiofiles
import aiofiles.os
import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Request
from googleapiclient.errors import HttpError

from tweebuilder.gcp_service import GCPService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Created once per app lifetime and reused across all requests.
    app.state.gcp_service = GCPService()
    app.state.db_pool = await asyncpg.create_pool(
        dsn=environ["DATABASE_URL"], min_size=2, max_size=30
    )
    app.state.scheduler = AsyncIOScheduler()
    app.state.scheduler.start()
    if environ["ENVIRONMENT"] != "development":
        await gdrive_watch_startup(app.state.gcp_service, app.state.scheduler)
    yield
    await app.state.db_pool.close()
    app.state.scheduler.shutdown()


GDRIVE_EXPIRATION_CACHE_FILENAME = "cache/gdrive_expiration.txt"


async def gdrive_watch_startup(gcp_service: GCPService, scheduler: AsyncIOScheduler):
    expiry: datetime | None = None
    if await aiofiles.os.path.exists(GDRIVE_EXPIRATION_CACHE_FILENAME):
        async with aiofiles.open(GDRIVE_EXPIRATION_CACHE_FILENAME, mode="r") as f:
            try:
                expiry = datetime.fromtimestamp(float(await f.read()), tz=UTC)
            except ValueError:
                expiry = None

    if not expiry:
        # Submit a new changes request and convert to seconds before saving to match python
        await gdrive_new_watch(gcp_service, scheduler)
    else:
        # We already have a changes request, wait until it expires
        scheduler.add_job(
            gdrive_new_watch, "date", run_date=expiry, args=[gcp_service, scheduler]
        )


async def gdrive_new_watch(gcp_service: GCPService, scheduler: AsyncIOScheduler):
    # Submit a new changes request to Google Drive and get the expiration time
    logger.info("Submitting new Google Drive watch request")

    try:
        body = {
            "id": str(uuid.uuid4()),
            "type": "web_hook",
            "expiration": 3000000000000,
            "address": "https://api.tylergarman.net/gdrive-webhook",
            "token": environ.get("GDRIVE_CHANNEL_TOKEN"),
        }
        result = gcp_service.files.watch(
            fileId=environ.get("GDRIVE_WATCH_FILE_ID"),
            body=body,
            acknowledgeAbuse=True,
            supportsAllDrives=True,
        ).execute()
        if expiration := result.get("expiration"):
            expiry = datetime.fromtimestamp(float(expiration) / 1000, tz=UTC)
        else:
            logger.warning("Failed to pull expiration time from the API")
    except HttpError:
        logger.exception("Failed to submit Google Drive watch request")

    if not expiry:
        # Just retry after 1 hour if we couldn't get the expiration from the API
        expiry = datetime.now(tz=UTC) + timedelta(seconds=3600)

    # Save the expiration time to the cache file
    async with aiofiles.open(GDRIVE_EXPIRATION_CACHE_FILENAME, mode="w") as f:
        await f.write(str(expiry.timestamp()))
    # Schedule the next watch startup
    scheduler.add_job(
        gdrive_new_watch, "date", run_date=expiry, args=[gcp_service, scheduler]
    )


def get_gcp_service(request: Request) -> GCPService:
    return request.app.state.gcp_service


def get_db_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


GCPServiceDep = Annotated[GCPService, Depends(get_gcp_service)]
PGPoolDep = Annotated[asyncpg.Pool, Depends(get_db_pool)]
