"""Motor client lifecycle. One client per process."""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from api.config import settings

_client: AsyncIOMotorClient[Any] | None = None


def get_client() -> AsyncIOMotorClient[Any]:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            settings().mongo_uri, tz_aware=True, uuidRepresentation="standard"
        )
    return _client


def get_db() -> AsyncIOMotorDatabase[Any]:
    return get_client()[settings().mongo_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
