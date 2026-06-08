import asyncio

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as rest


async def check() -> None:
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        "test", vectors_config=rest.VectorParams(size=1, distance=rest.Distance.COSINE)
    )
    await client.upsert(
        "test", points=[rest.PointStruct(id=1, vector=[0.1], payload={"job_id": "j1"})]
    )
    # Use query method (which is query_points alias or similar)
    res = await client.query_points("test", query=[0.1], limit=1)
    print("Res type:", type(res))
    print("Res points:", res.points)
    await client.close()


if __name__ == "__main__":
    asyncio.run(check())
