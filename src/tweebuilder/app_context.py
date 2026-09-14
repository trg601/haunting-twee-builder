from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from tweebuilder.gcp_service import GCPService


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Created once per app lifetime and reused across all requests.
    app.state.gcp_service = GCPService()
    yield


def get_gcp_service(request: Request) -> GCPService:
    return request.app.state.gcp_service


GCPServiceDep = Annotated[GCPService, Depends(get_gcp_service)]
