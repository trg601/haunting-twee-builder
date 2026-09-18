import json
import logging
import os

import aiofiles
import aiofiles.os
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents",
]


def resolve_gcp_credentials() -> Credentials:
    creds: Credentials | None = None
    if os.path.exists("service-account.json"):
        creds = Credentials.from_service_account_file(
            "service-account.json", scopes=SCOPES
        )
    else:
        raise RuntimeError("No service account credentials found.")
    # If there are no (valid) credentials available, let the user log in.
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


class GCPService:
    def __init__(self):
        self.file_id_cache: dict = {}
        self.cache_filename = "cache/file_id_cache.json"
        self.creds = resolve_gcp_credentials()

        if not os.path.exists("cache"):
            os.makedirs("cache")

        # https://googleapis.github.io/google-api-python-client/docs/dyn/drive_v3.html
        self.drive_service = build("drive", "v3", credentials=self.creds)
        # https://googleapis.github.io/google-api-python-client/docs/dyn/docs_v1.html
        self.docs_service = build("docs", "v1", credentials=self.creds)

        self.files = self.drive_service.files()
        self.docs = self.docs_service.documents()

    async def get_file_by_id(
        self, file_id: str, use_cache: bool = False
    ) -> dict | None:
        if use_cache and await aiofiles.os.path.exists(f"cache/{file_id}.json"):
            # If not expecting changes and we have it cached, use the cached version
            async with aiofiles.open(f"cache/{file_id}.json", "r") as f:
                return json.loads(await f.read())
        document = self.docs.get(documentId=file_id, includeTabsContent=True).execute()
        # Add to cache if this is a new document retrieved from another source (e.g. the webhook)
        if (
            document
            and (title := document.get("title"))
            and title not in self.file_id_cache
        ):
            await self.add_file_to_cache(title, file_id)
        # Save file contents to cache
        async with aiofiles.open(f"cache/{file_id}.json", "w") as f:
            await f.write(json.dumps(obj=document, indent=4))
        return document

    async def get_file_by_name(self, name: str, use_cache: bool = False) -> dict | None:
        file_id = await self._get_file_id_by_name(name)
        if not file_id:
            return None
        return await self.get_file_by_id(file_id, use_cache)

    async def _get_file_id_by_name(
        self,
        name: str,
        mime_type: str = "application/vnd.google-apps.document",
    ) -> str | None:
        """Get a file ID by name and mime type."""
        # Note: folder mimetype is application/vnd.google-apps.folder

        if not self.file_id_cache and await aiofiles.os.path.exists(
            self.cache_filename
        ):
            async with aiofiles.open(self.cache_filename, "r") as f:
                self.file_id_cache = json.loads(await f.read())

        if file_id := self.file_id_cache.get(name):
            return file_id

        try:
            results = self.files.list(
                pageSize=1,
                q=f"name = '{name}' and mimeType = '{mime_type}'",
                fields="files(id)",
            ).execute()
            items = results.get("files", [])
            if not items:
                logger.warning("No file found with name: %s", name)
                return None
            file_id = items[0].get("id")
            # Update cache
            await self.add_file_to_cache(name, file_id)
            return file_id
        except HttpError:
            logger.exception("An error occurred")
            return None

    async def add_file_to_cache(self, name: str, file_id: str):
        self.file_id_cache[name] = file_id
        async with aiofiles.open(self.cache_filename, "w") as f:
            await f.write(json.dumps(self.file_id_cache, indent=4))
