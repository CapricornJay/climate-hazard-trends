"""Persistence. MongoDB in production, a dict-backed twin for tests.

Collections
  series    one document per (source, variable, grid cell or station). The
            daily values are stored as a zlib-compressed int16 array in tenths
            of a unit: 65 years of Tmax is ~25 KB instead of ~400 KB as a BSON
            array, which matters on a 512 MB free cluster.
  analyses  finished analyses keyed by a hash of the normalised request, so
            repeat requests (and shared links) are a single _id lookup.
  lookups   small cached API responses (place search, state polygons, station
            lists) with a TTL index.
"""

import copy
import time
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

MISSING = -32768
SCALE = 10  # store tenths


def pack(values: np.ndarray) -> bytes:
    v = np.where(np.isnan(values), MISSING, np.round(values * SCALE))
    v = np.clip(v, MISSING, 32767).astype("<i2")
    return zlib.compress(v.tobytes(), 6)


def unpack(blob: bytes) -> np.ndarray:
    raw = np.frombuffer(zlib.decompress(blob), dtype="<i2")
    out = raw.astype(float) / SCALE
    out[raw == MISSING] = np.nan
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoStore:
    def __init__(self, uri: str, db_name: str):
        from pymongo import AsyncMongoClient

        self.client = AsyncMongoClient(uri, serverSelectionTimeoutMS=8000, appname="climate-hazard-trends", tz_aware=True)
        self.db = self.client[db_name]

    async def init(self) -> None:
        await self.db.lookups.create_index("expires_at", expireAfterSeconds=0)
        await self.db.analyses.create_index("expires_at", expireAfterSeconds=0)
        await self.db.series.create_index([("source", 1), ("variable", 1)])

    async def close(self) -> None:
        await self.client.close()

    async def ping(self) -> bool:
        try:
            await self.db.command("ping")
            return True
        except Exception:
            return False

    async def get_series_many(self, keys: list[str]) -> dict[str, dict]:
        docs = await self.db.series.find({"_id": {"$in": keys}}).to_list(None)
        return {d["_id"]: d for d in docs}

    async def put_series(self, doc: dict) -> None:
        await self.db.series.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_analysis(self, analysis_id: str) -> dict | None:
        return await self.db.analyses.find_one({"_id": analysis_id})

    async def put_analysis(self, doc: dict, ttl_days: int = 60) -> None:
        doc = {**doc, "expires_at": _now() + timedelta(days=ttl_days)}
        await self.db.analyses.replace_one({"_id": doc["_id"]}, doc, upsert=True)

    async def get_lookup(self, key: str):
        doc = await self.db.lookups.find_one({"_id": key})
        # The TTL monitor only runs once a minute, so check expiry ourselves too.
        if not doc or doc["expires_at"] < _now():
            return None
        return doc["value"]

    async def put_lookup(self, key: str, value, ttl_seconds: int) -> None:
        await self.db.lookups.replace_one(
            {"_id": key},
            {"_id": key, "value": value, "expires_at": _now() + timedelta(seconds=ttl_seconds)},
            upsert=True,
        )


class MemoryStore:
    """Same interface, no persistence. Used by the tests."""

    def __init__(self):
        self.series: dict[str, dict] = {}
        self.analyses: dict[str, dict] = {}
        self.lookups: dict[str, tuple[float, object]] = {}

    async def init(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def get_series_many(self, keys):
        return {k: copy.deepcopy(self.series[k]) for k in keys if k in self.series}

    async def put_series(self, doc):
        self.series[doc["_id"]] = copy.deepcopy(doc)

    async def get_analysis(self, analysis_id):
        doc = self.analyses.get(analysis_id)
        return copy.deepcopy(doc) if doc else None

    async def put_analysis(self, doc, ttl_days: int = 60):
        self.analyses[doc["_id"]] = copy.deepcopy(doc)

    async def get_lookup(self, key):
        hit = self.lookups.get(key)
        if not hit or hit[0] < time.time():
            return None
        return copy.deepcopy(hit[1])

    async def put_lookup(self, key, value, ttl_seconds):
        self.lookups[key] = (time.time() + ttl_seconds, copy.deepcopy(value))
