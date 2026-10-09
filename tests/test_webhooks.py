import asyncio
import itertools
import json

import pytest
from sqlalchemy import update

from app.enums import PaymentStatus
from app.models import Payment
from tests.helpers import count_payments, payment_body, post_webhook, sign

PENDING = PaymentStatus.PENDING
SUCCEEDED = PaymentStatus.SUCCEEDED
FAILED = PaymentStatus.FAILED
REFUNDED = PaymentStatus.REFUNDED

# Переходы из ТЗ: pending -> succeeded, pending -> failed, succeeded -> refunded.
ALLOWED = [(PENDING, SUCCEEDED), (PENDING, FAILED), (SUCCEEDED, REFUNDED)]
FORBIDDEN = [pair for pair in itertools.product(PaymentStatus, repeat=2) if pair not in ALLOWED]


async def create_payment(client) -> int:
    response = await client.post("/payments", json=payment_body())
    return response.json()["id"]


async def set_status_in_db(session_factory, payment_id: int, status: PaymentStatus) -> None:
    """Подготовка исходного статуса напрямую в базе, в обход вебхука."""
    async with session_factory() as session:
        await session.execute(update(Payment).where(Payment.id == payment_id).values(status=status))
        await session.commit()


async def send_signed(client, body: dict):
    """Вебхук с верной подписью. Саму подпись проверяют тесты в test_webhook_signature.py."""
    raw_body = json.dumps(body).encode()
    return await post_webhook(client, raw_body, sign(raw_body))


async def send_webhook(client, payment_id: int, status: str):
    return await send_signed(client, {"payment_id": payment_id, "status": status})


async def get_status(client, payment_id: int) -> str:
    return (await client.get(f"/payments/{payment_id}")).json()["status"]


# --- разрешённые переходы ---


@pytest.mark.parametrize(("start", "new"), ALLOWED)
async def test_allowed_transition_changes_status(client, session_factory, start, new):
    payment_id = await create_payment(client)
    await set_status_in_db(session_factory, payment_id, start)

    response = await send_webhook(client, payment_id, new.value)

    assert response.status_code == 200
    assert response.json() == {"result": "ok"}
    assert await get_status(client, payment_id) == new.value


async def test_full_chain_pending_succeeded_refunded(client):
    payment_id = await create_payment(client)

    assert (await send_webhook(client, payment_id, "succeeded")).status_code == 200
    assert await get_status(client, payment_id) == "succeeded"
    assert (await send_webhook(client, payment_id, "refunded")).status_code == 200
    assert await get_status(client, payment_id) == "refunded"


async def test_webhook_changes_only_status_of_the_given_payment(client):
    first_id = await create_payment(client)
    second_id = await create_payment(client)
    before = (await client.get(f"/payments/{first_id}")).json()
    other_before = (await client.get(f"/payments/{second_id}")).json()

    await send_webhook(client, first_id, "succeeded")

    after = (await client.get(f"/payments/{first_id}")).json()
    assert after == {**before, "status": "succeeded"}  # остальные поля не тронуты
    assert (await client.get(f"/payments/{second_id}")).json() == other_before


# --- запрещённые переходы ---


@pytest.mark.parametrize(("start", "new"), FORBIDDEN)
async def test_forbidden_transition_is_409_and_status_is_unchanged(
    client, session_factory, start, new
):
    payment_id = await create_payment(client)
    await set_status_in_db(session_factory, payment_id, start)

    response = await send_webhook(client, payment_id, new.value)

    assert response.status_code == 409
    assert response.json() == {"error": "invalid_transition"}
    assert await get_status(client, payment_id) == start.value


def test_forbidden_list_covers_everything_except_the_three_allowed():
    assert len(FORBIDDEN) == 16 - 3


# --- платежа нет, невалидное тело ---


async def test_webhook_for_missing_payment_is_404(client, session_factory):
    response = await send_webhook(client, 999, "succeeded")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}
    assert await count_payments(session_factory) == 0


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"payment_id": 1, "status": "paid"}, "status"),
        ({"payment_id": 1, "status": ""}, "status"),
        ({"payment_id": 1}, "status"),
        ({"status": "succeeded"}, "payment_id"),
        ({"payment_id": "abc", "status": "succeeded"}, "payment_id"),
        ({"payment_id": 0, "status": "succeeded"}, "payment_id"),
        ({"payment_id": -1, "status": "succeeded"}, "payment_id"),
        ({"payment_id": 99999999999, "status": "succeeded"}, "payment_id"),
    ],
)
async def test_invalid_body_is_422_and_status_is_unchanged(client, body, field):
    payment_id = await create_payment(client)

    response = await send_signed(client, body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]
    assert await get_status(client, payment_id) == "pending"


# --- гонки ---


async def test_concurrent_conflicting_webhooks_only_one_wins(client):
    payment_id = await create_payment(client)

    responses = await asyncio.gather(
        send_webhook(client, payment_id, "succeeded"),
        send_webhook(client, payment_id, "failed"),
    )

    codes = sorted(response.status_code for response in responses)
    assert codes == [200, 409]
    winner = "succeeded" if responses[0].status_code == 200 else "failed"
    assert await get_status(client, payment_id) == winner


async def test_concurrent_identical_webhooks_only_one_succeeds(client):
    payment_id = await create_payment(client)

    responses = await asyncio.gather(
        *[send_webhook(client, payment_id, "succeeded") for _ in range(10)]
    )

    assert sorted(response.status_code for response in responses) == [200] + [409] * 9
    assert await get_status(client, payment_id) == "succeeded"
