from sqlalchemy import func, select

from app.db import seed_tariffs
from app.models import Tariff


async def test_get_tariffs_returns_three_tariffs_in_kopecks(client):
    response = await client.get("/tariffs")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "basic", "title": "Basic", "price": 990000},
        {"id": "standard", "title": "Standard", "price": 1990000},
        {"id": "premium", "title": "Premium", "price": 2990000},
    ]


async def test_seeding_twice_does_not_duplicate_tariffs(client, session_factory):
    # lifespan уже посеял тарифы при старте приложения; повторяем, как при перезапуске.
    async with session_factory() as session:
        await seed_tariffs(session)

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(Tariff))
    assert count == 3

    response = await client.get("/tariffs")
    assert len(response.json()) == 3
