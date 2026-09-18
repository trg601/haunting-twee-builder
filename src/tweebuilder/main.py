import asyncio
import logging
import os
from typing import Annotated

from tweebuilder.auth import AuthenticateUserDep, GDriveChannelTokenDep, auth_router
from tweebuilder.static_files import static_router

if os.name == "nt":
    from asyncio import ProactorEventLoop as EventLoopFactory
else:
    from asyncio import SelectorEventLoop as EventLoopFactory

from fastapi import FastAPI, Header
from googleapiclient.errors import HttpError
from uvicorn.config import Config
from uvicorn.server import Server
from uvicorn.supervisors import ChangeReload

from tweebuilder.app_context import GCPServiceDep, lifespan
from tweebuilder.generate_twee import generate_twee
from tweebuilder.twine_config import global_twine_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)
app.include_router(static_router)


@app.post("/build")
async def build(gcp_service: GCPServiceDep, _: AuthenticateUserDep):
    try:
        await generate_twee(gcp_service)
        return {"status": "success"}
    except HttpError:
        logger.exception("An error occurred")
    return {"status": "failure"}


@app.post("/gdrive-webhook")
async def gdrive_webhook(
    gcp_service: GCPServiceDep,
    _: GDriveChannelTokenDep,
    x_goog_resource_uri: Annotated[str | None, Header()] = None,
    x_goog_resource_state: Annotated[str | None, Header()] = None,
):
    # Handle webhook notifications from Google Drive

    # parse resource id from format https://www.googleapis.com/drive/v3/files/{resource_id}
    resource_id = (
        x_goog_resource_uri.split("/")[-1]
        if x_goog_resource_uri and "/files/" in x_goog_resource_uri
        else None
    )
    if not resource_id or resource_id not in global_twine_config.watch_list:
        # Just ignoring to avoid excessing logging, please do not bite me in the ass >:(
        return {"status": "ignored"}

    logger.info(
        "Received Google Drive webhook (%s) for resource ID: %s",
        x_goog_resource_state,
        resource_id,
    )
    # Download document and save to cache
    try:
        file_data = await gcp_service.get_file_by_id(resource_id, use_cache=False)
        if file_data:
            logger.info(
                "File data for resource ID %s retrieved successfully. Rebuilding project...",
                resource_id,
            )
            await generate_twee(gcp_service)
    except HttpError:
        logger.exception(
            "An error occurred while retrieving file data for resource ID %s",
            resource_id,
        )

    return {"status": "received"}


class Server(Server):
    def run(self, sockets=None):
        asyncio.run(
            self.serve(),
            loop_factory=EventLoopFactory,
        )


if __name__ == "__main__":
    config = Config(
        app="tweebuilder.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
    server = Server(config=config)
    ChangeReload(config, target=server.run, sockets=[]).run()
