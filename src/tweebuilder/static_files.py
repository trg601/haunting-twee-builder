from fastapi import APIRouter, Request
from fastapi.staticfiles import StaticFiles

static_router = APIRouter(include_in_schema=False, prefix="/static")

static_files = StaticFiles(directory="build", html=True)


@static_router.get("/{file_path:path}")
async def read_static(file_path: str, request: Request):
    return await static_files.get_response(file_path, request.scope)
