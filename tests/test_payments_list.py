import pytest

from app.enums import PaymentStatus
from tests.helpers import payment_body, send_webhook, set_status_in_db


async def create_payment(client, **overrides) -> dict:
    response = await client.post("/payments", json=payment_body(**overrides))
    return response.json()


def ids(response) -> list[int]:
    return [payment["id"] for payment in response.json()]


async def test_empty_list_when_no_payments(client):
    response = await client.get("/payments")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_returns_all_payments_in_creation_order(client):
    first = await create_payment(client, tariff_id="basic")
    second = await create_payment(client, method="installment", installment_months=3)
    third = await create_payment(client, promo_code="KVITTO10")

    response = await client.get("/payments")

    assert response.status_code == 200
    # Полные объекты, как в ответе POST, включая график рассрочки.
    assert response.json() == [first, second, third]
    assert response.json()[1]["schedule"] == [663_334, 663_333, 663_333]


async def test_order_stays_by_id_after_rows_are_updated(client, session_factory):
    # UPDATE в Postgres записывает новую версию строки в конец таблицы. Без ORDER BY
    # обновлённый платёж оказался бы в конце списка, а не на своём месте.
    first = await create_payment(client)
    second = await create_payment(client)
    third = await create_payment(client)
    await set_status_in_db(session_factory, first["id"], PaymentStatus.SUCCEEDED)

    response = await client.get("/payments")

    assert ids(response) == [first["id"], second["id"], third["id"]]


async def test_filter_by_email_returns_only_that_email(client):
    mine = await create_payment(client, email="anna@example.com")
    await create_payment(client, email="boris@example.com")
    mine_again = await create_payment(client, email="anna@example.com")

    response = await client.get("/payments", params={"email": "anna@example.com"})

    assert response.json() == [mine, mine_again]


@pytest.mark.parametrize(
    "email", ["ANNA@example.com", "anna", "anna@example", "other@example.com", ""]
)
async def test_email_filter_is_exact_match(client, email):
    await create_payment(client, email="anna@example.com")

    response = await client.get("/payments", params={"email": email})

    assert response.status_code == 200
    assert response.json() == []


async def test_email_with_plus_sign_is_matched(client):
    payment = await create_payment(client, email="anna+school@example.com")
    await create_payment(client, email="anna@example.com")

    response = await client.get("/payments", params={"email": "anna+school@example.com"})

    assert response.json() == [payment]


async def test_filter_by_status(client, session_factory):
    pending = await create_payment(client)
    succeeded = await create_payment(client)
    failed = await create_payment(client)
    await set_status_in_db(session_factory, succeeded["id"], PaymentStatus.SUCCEEDED)
    await set_status_in_db(session_factory, failed["id"], PaymentStatus.FAILED)

    assert ids(await client.get("/payments", params={"status": "pending"})) == [pending["id"]]
    assert ids(await client.get("/payments", params={"status": "succeeded"})) == [succeeded["id"]]
    assert ids(await client.get("/payments", params={"status": "failed"})) == [failed["id"]]
    assert ids(await client.get("/payments", params={"status": "refunded"})) == []


async def test_filters_are_combined_with_and(client, session_factory):
    anna_pending = await create_payment(client, email="anna@example.com")
    anna_succeeded = await create_payment(client, email="anna@example.com")
    boris_succeeded = await create_payment(client, email="boris@example.com")
    await set_status_in_db(session_factory, anna_succeeded["id"], PaymentStatus.SUCCEEDED)
    await set_status_in_db(session_factory, boris_succeeded["id"], PaymentStatus.SUCCEEDED)

    response = await client.get(
        "/payments", params={"email": "anna@example.com", "status": "succeeded"}
    )

    assert ids(response) == [anna_succeeded["id"]]
    assert anna_pending["id"] not in ids(response)


async def test_status_filter_reflects_webhook_changes(client):
    payment = await create_payment(client)
    await send_webhook(client, payment["id"], "succeeded")

    assert ids(await client.get("/payments", params={"status": "pending"})) == []
    succeeded = await client.get("/payments", params={"status": "succeeded"})
    assert ids(succeeded) == [payment["id"]]
    assert succeeded.json()[0]["status"] == "succeeded"


@pytest.mark.parametrize("status", ["paid", "", "PENDING"])
async def test_invalid_status_filter_is_422(client, status):
    await create_payment(client)

    response = await client.get("/payments", params={"status": status})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "status"]


async def test_get_payment_by_id_still_works_next_to_list(client):
    payment = await create_payment(client)

    response = await client.get(f"/payments/{payment['id']}")

    assert response.status_code == 200
    assert response.json() == payment
