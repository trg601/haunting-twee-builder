import asyncio
import os
from typing import Annotated

if os.name == "nt":
    from asyncio import ProactorEventLoop as EventLoopFactory
else:
    from asyncio import SelectorEventLoop as EventLoopFactory

from fastapi import FastAPI, Header
from fastapi.staticfiles import StaticFiles
from googleapiclient.errors import HttpError
from uvicorn.config import Config
from uvicorn.server import Server
from uvicorn.supervisors import ChangeReload

from tweebuilder.app_context import GCPServiceDep, lifespan
from tweebuilder.generate_twee import generate_twee
from tweebuilder.twine_config import global_twine_config

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="build"), name="static")


@app.post("/build")
async def build(gcp_service: GCPServiceDep):
    try:
        await generate_twee(gcp_service)
        return {"status": "success"}
    except HttpError as error:
        print(f"An error occurred: {error}")
    return {"status": "failure"}


@app.post("/gdrive-webhook")
async def gdrive_webhook(
    gcp_service: GCPServiceDep,
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

    print(
        f"Received Google Drive webhook ({x_goog_resource_state}) for resource ID: {resource_id}"
    )
    # Download document and save to cache
    try:
        file_data = await gcp_service.get_file_by_id(resource_id, use_cache=False)
        if file_data:
            print(
                f"File data for resource ID {resource_id} retrieved successfully. Rebuilding project..."
            )
            await generate_twee(gcp_service)
    except HttpError as error:
        print(
            f"An error occurred while retrieving file data for resource ID {resource_id}: {error}"
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
