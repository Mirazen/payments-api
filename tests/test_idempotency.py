import asyncio

import pytest
from sqlalchemy import select, update

from app.enums import PaymentStatus
from app.models import Payment
from app.routers import payments as payments_router
from tests.test_payments import count_payments, payment_body


def with_key(key: str) -> dict:
    return {"Idempotency-Key": key}


async def test_same_key_returns_same_payment_with_200(client, session_factory):
    first = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))
    second = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert await count_payments(session_factory) == 1


async def test_key_is_saved_with_payment(client, session_factory):
    response = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    async with session_factory() as session:
        payment = await session.get(Payment, response.json()["id"])
    assert payment.idempotency_key == "key-1"


async def test_different_keys_create_different_payments(client, session_factory):
    first = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))
    second = await client.post("/payments", json=payment_body(), headers=with_key("key-2"))

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]
    assert await count_payments(session_factory) == 2


@pytest.mark.parametrize(
    "other_body",
    [
        {"tariff_id": "premium"},
        {"method": "installment", "installment_months": 12},
        {"promo_code": "KVITTO10"},
        {"email": "other@example.com"},
        {"tariff_id": "gold"},  # тариф не существует, но ключ уже известен
    ],
)
async def test_same_key_with_other_body_returns_original_payment(
    client, session_factory, other_body
):
    first = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))
    second = await client.post(
        "/payments", json=payment_body(**other_body), headers=with_key("key-1")
    )

    assert second.status_code == 200
    assert second.json() == first.json()  # данные первого запроса, а не второго
    assert await count_payments(session_factory) == 1


async def test_body_failing_schema_validation_is_422_even_with_known_key(client):
    await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    response = await client.post(
        "/payments", json=payment_body(email="not-an-email"), headers=with_key("key-1")
    )

    assert response.status_code == 422


async def test_repeat_returns_current_status_of_payment(client, session_factory):
    first = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))
    async with session_factory() as session:
        await session.execute(
            update(Payment)
            .where(Payment.id == first.json()["id"])
            .values(status=PaymentStatus.SUCCEEDED)
        )
        await session.commit()

    second = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    assert second.status_code == 200
    assert second.json()["status"] == "succeeded"


async def test_failed_request_does_not_burn_the_key(client, session_factory):
    failed = await client.post(
        "/payments", json=payment_body(promo_code="WRONG"), headers=with_key("key-1")
    )
    assert failed.status_code == 422
    assert await count_payments(session_factory) == 0

    retry = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    assert retry.status_code == 201
    assert await count_payments(session_factory) == 1


@pytest.mark.parametrize("key", ["", "x" * 256])
async def test_empty_or_too_long_key_is_422(client, session_factory, key):
    response = await client.post("/payments", json=payment_body(), headers=with_key(key))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["header", "idempotency-key"]
    assert await count_payments(session_factory) == 0


async def test_key_of_max_length_is_accepted(client):
    response = await client.post("/payments", json=payment_body(), headers=with_key("x" * 255))

    assert response.status_code == 201


async def test_concurrent_requests_with_same_key_create_one_payment(client, session_factory):
    responses = await asyncio.gather(
        *[
            client.post("/payments", json=payment_body(), headers=with_key("key-1"))
            for _ in range(10)
        ]
    )

    assert sorted(response.status_code for response in responses) == [200] * 9 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert await count_payments(session_factory) == 1


async def test_unique_conflict_on_insert_returns_existing_payment(
    client, session_factory, monkeypatch
):
    """Детерминированная проверка гонки.

    Первый поиск по ключу "не видит" платёж, как будто параллельный запрос ещё не
    успел закоммитить. Вставка упирается в уникальное ограничение, и обработчик
    должен откатить транзакцию, перечитать платёж и вернуть 200.
    """
    first = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    real_find = payments_router.find_payment_by_key
    calls = []

    async def find_blind_first_time(session, key):
        calls.append(key)
        if len(calls) == 1:
            return None
        return await real_find(session, key)

    monkeypatch.setattr(payments_router, "find_payment_by_key", find_blind_first_time)

    second = await client.post("/payments", json=payment_body(), headers=with_key("key-1"))

    assert len(calls) == 2  # поиск до вставки и повторный поиск после отката
    assert second.status_code == 200
    assert second.json() == first.json()
    async with session_factory() as session:
        ids = (await session.scalars(select(Payment.id))).all()
    assert ids == [first.json()["id"]]
