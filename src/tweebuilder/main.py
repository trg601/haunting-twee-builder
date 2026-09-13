import asyncio
import json
import os

import aiofiles
import aiofiles.os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tweebuilder.generate_twee import generate_twee

# If modifying these scopes, delete the file token.json.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/documents",
]

file_id_cache: dict | None = None


def resolve_gcp_credentials() -> Credentials:
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    return creds


async def get_file_id_by_name(
    service, name: str, mime_type: str = "application/vnd.google-apps.document"
) -> str | None:
    """Get a file ID by name and mime type."""
    global file_id_cache
    cache_filename = "cache/file_id_cache.json"

    if file_id_cache is None and await aiofiles.os.path.exists(cache_filename):
        async with aiofiles.open(cache_filename, "r") as f:
            file_id_cache = json.loads(await f.read())
    else:
        file_id_cache = {}

    if file_id := file_id_cache.get(name):
        return file_id

    try:
        results = (
            service.files()
            .list(
                pageSize=1,
                q=f"name = '{name}' and mimeType = '{mime_type}'",
                fields="files(id)",
            )
            .execute()
        )
        items = results.get("files", [])
        if not items:
            print(f"No file found with name: {name}")
            return None
        file_id = items[0].get("id")
        # Update cache
        file_id_cache[name] = file_id
        async with aiofiles.open(cache_filename, "w") as f:
            await f.write(json.dumps(file_id_cache, indent=4))
        return file_id
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None


async def main() -> None:
    # TODO: TEMP: Load from local instead of contacting Google Drive API
    async with aiofiles.open("cache/outline_data.json", "r") as f:
        outline_data = json.loads(await f.read())
        await generate_twee(outline_data)
        return

    creds = resolve_gcp_credentials()

    try:
        # https://googleapis.github.io/google-api-python-client/docs/dyn/drive_v3.html
        drive_service = build("drive", "v3", credentials=creds)
        # https://googleapis.github.io/google-api-python-client/docs/dyn/docs_v1.html
        docs_service = build("docs", "v1", credentials=creds)

        files = drive_service.files()
        docs = docs_service.documents()

        # Find the Haunting Music Folder by name
        # files.list(pageSize=1, q="name = 'Haunting Music' and mimeType = 'application/vnd.google-apps.folder'", fields="files(id)").execute()
        # results = files.list(
        #     pageSize=50, fields="nextPageToken, files(id, name)"
        # ).execute()
        # items = results.get("files", [])

        # Download the Outline file
        file_id = await get_file_id_by_name(drive_service, "Act 2 outline")

        outline_data = docs.get(documentId=file_id, includeTabsContent=True).execute()
        if outline_data:
            print("Outline data retrieved successfully.")
            async with aiofiles.open("cache/outline_data.json", "w") as f:
                await f.write(json.dumps(obj=outline_data, indent=4))

            # Generate simplified branching structure from tabs
            await generate_twee(outline_data)

    except HttpError as error:
        print(f"An error occurred: {error}")


if __name__ == "__main__":
    asyncio.run(main())
