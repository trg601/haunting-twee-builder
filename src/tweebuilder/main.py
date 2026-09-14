import asyncio
import json

import aiofiles
import aiofiles.os
from googleapiclient.errors import HttpError

from tweebuilder.gcp_service import GCPService
from tweebuilder.generate_twee import generate_twee


async def main() -> None:
    # TODO: TEMP: Load from local instead of contacting Google Drive API
    # async with aiofiles.open("cache/outline_data.json", "r") as f:
    #     outline_data = json.loads(await f.read())
    #     await generate_twee(outline_data)
    #     return

    gcp_service = GCPService()

    try:
        # Download the Outline file
        outline_data = await gcp_service.get_file_by_name("Act 2 outline")
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
