import asyncio

from tweebuilder.gcp_service import GCPService
from tweebuilder.generate_twee import generate_twee


async def main() -> None:
    gcp_service = GCPService()
    await generate_twee(gcp_service)


if __name__ == "__main__":
    asyncio.run(main())
