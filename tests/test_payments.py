from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.enums import PaymentMethod, PaymentStatus
from app.models import Payment

TARIFF_PRICES = {"basic": 990_000, "standard": 1_990_000, "premium": 2_990_000}

PAYMENT_FIELDS = {
    "id",
    "status",
    "tariff_id",
    "amount",
    "discount",
    "method",
    "installment_months",
    "schedule",
    "email",
    "created_at",
}


def payment_body(**overrides):
    """Корректное тело запроса: standard, карта. Тесты меняют только нужные поля."""
    body = {"tariff_id": "standard", "email": "student@example.com", "method": "card"}
    body.update(overrides)
    return body


async def count_payments(session_factory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Payment))


# --- создание: суммы и промокод ---


async def test_create_payment_without_promo(client):
    response = await client.post("/payments", json=payment_body())

    assert response.status_code == 201
    body = response.json()
    assert set(body) == PAYMENT_FIELDS
    assert isinstance(body["id"], int)
    assert body["created_at"]  # проверяем формат отдельным тестом
    del body["id"], body["created_at"]
    assert body == {
        "status": "pending",
        "tariff_id": "standard",
        "amount": 1_990_000,
        "discount": 0,
        "method": "card",
        "installment_months": None,
        "schedule": None,
        "email": "student@example.com",
    }


@pytest.mark.parametrize("promo_code", ["KVITTO10", "kvitto10", "KvItTo10"])
async def test_promo_gives_ten_percent_in_any_case(client, promo_code):
    response = await client.post("/payments", json=payment_body(promo_code=promo_code))

    assert response.status_code == 201
    body = response.json()
    assert body["discount"] == 199_000
    assert body["amount"] == 1_791_000


@pytest.mark.parametrize(("tariff_id", "price"), TARIFF_PRICES.items())
@pytest.mark.parametrize("promo_code", [None, "KVITTO10"])
async def test_amount_plus_discount_equals_tariff_price(client, tariff_id, price, promo_code):
    response = await client.post(
        "/payments", json=payment_body(tariff_id=tariff_id, promo_code=promo_code)
    )

    body = response.json()
    assert body["amount"] + body["discount"] == price
    assert (body["discount"] == 0) == (promo_code is None)
    # В JSON должны быть целые числа, а не 891000.0
    assert type(body["amount"]) is int
    assert type(body["discount"]) is int


async def test_client_cannot_set_amount_or_discount(client):
    response = await client.post("/payments", json=payment_body(amount=1, discount=1_990_000))

    assert response.status_code == 201
    assert response.json()["amount"] == 1_990_000
    assert response.json()["discount"] == 0


@pytest.mark.parametrize("promo_code", ["", " ", " KVITTO10", "KVITTO10 ", "UNKNOWN"])
async def test_unknown_promo_is_422_and_nothing_is_saved(client, session_factory, promo_code):
    response = await client.post("/payments", json=payment_body(promo_code=promo_code))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "promo_code"]
    assert await count_payments(session_factory) == 0


# --- создание: рассрочка ---


async def test_installment_schedule_from_spec_example(client):
    response = await client.post(
        "/payments", json=payment_body(method="installment", installment_months=3)
    )

    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == 1_990_000
    assert body["installment_months"] == 3
    assert body["schedule"] == [663_334, 663_333, 663_333]


@pytest.mark.parametrize("promo_code", [None, "KVITTO10"])
@pytest.mark.parametrize("months", [3, 6, 12])
@pytest.mark.parametrize("tariff_id", TARIFF_PRICES)
async def test_schedule_sums_to_amount(client, tariff_id, months, promo_code):
    response = await client.post(
        "/payments",
        json=payment_body(
            tariff_id=tariff_id,
            method="installment",
            installment_months=months,
            promo_code=promo_code,
        ),
    )

    body = response.json()
    assert len(body["schedule"]) == months
    assert sum(body["schedule"]) == body["amount"]
    assert all(type(payment) is int for payment in body["schedule"])


@pytest.mark.parametrize("method", ["card", "sbp"])
async def test_card_and_sbp_have_no_schedule(client, method):
    response = await client.post("/payments", json=payment_body(method=method))

    assert response.status_code == 201
    assert response.json()["method"] == method
    assert response.json()["installment_months"] is None
    assert response.json()["schedule"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"method": "installment"},  # срок не передан
        {"method": "installment", "installment_months": None},
        {"method": "installment", "installment_months": 0},
        {"method": "installment", "installment_months": 1},
        {"method": "installment", "installment_months": 24},
        {"method": "installment", "installment_months": -3},
        {"method": "card", "installment_months": 6},  # срок только для рассрочки
        {"method": "sbp", "installment_months": 3},
    ],
)
async def test_invalid_installment_months_is_422_and_nothing_is_saved(
    client, session_factory, overrides
):
    response = await client.post("/payments", json=payment_body(**overrides))

    assert response.status_code == 422
    assert await count_payments(session_factory) == 0


# --- создание: остальная валидация ---


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"tariff_id": "gold"}, "tariff_id"),
        ({"tariff_id": "BASIC"}, "tariff_id"),
        ({"method": "cash"}, "method"),
        ({"email": "not-an-email"}, "email"),
        ({"email": ""}, "email"),
    ],
)
async def test_invalid_field_is_422_and_nothing_is_saved(client, session_factory, overrides, field):
    response = await client.post("/payments", json=payment_body(**overrides))

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]
    assert await count_payments(session_factory) == 0


@pytest.mark.parametrize("missing", ["tariff_id", "email", "method"])
async def test_missing_required_field_is_422(client, missing):
    body = payment_body()
    del body[missing]

    response = await client.post("/payments", json=body)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", missing]


# --- создание: сохранение в базу ---


async def test_created_payment_is_saved_to_database(client, session_factory):
    response = await client.post(
        "/payments",
        json=payment_body(
            tariff_id="premium",
            method="installment",
            installment_months=6,
            promo_code="kvitto10",
        ),
    )

    payment_id = response.json()["id"]
    # Отдельная сессия: тест не делит сессию с приложением.
    async with session_factory() as session:
        payment = await session.get(Payment, payment_id)
    assert payment.status == PaymentStatus.PENDING
    assert payment.tariff_id == "premium"
    assert payment.amount == 2_691_000
    assert payment.discount == 299_000
    assert payment.method == PaymentMethod.INSTALLMENT
    assert payment.installment_months == 6
    assert payment.email == "student@example.com"
    assert payment.idempotency_key is None


async def test_created_at_is_utc_iso_datetime(client):
    response = await client.post("/payments", json=payment_body())

    created_at = datetime.fromisoformat(response.json()["created_at"])
    assert created_at.utcoffset().total_seconds() == 0


async def test_same_request_without_key_creates_two_payments(client, session_factory):
    first = await client.post("/payments", json=payment_body())
    second = await client.post("/payments", json=payment_body())

    assert first.json()["id"] != second.json()["id"]
    assert await count_payments(session_factory) == 2


# --- чтение ---


async def test_get_payment_returns_all_fields_same_as_created(client):
    created = await client.post(
        "/payments",
        json=payment_body(method="installment", installment_months=3, promo_code="KVITTO10"),
    )

    response = await client.get(f"/payments/{created.json()['id']}")

    assert response.status_code == 200
    assert set(response.json()) == PAYMENT_FIELDS
    assert response.json() == created.json()


async def test_get_missing_payment_is_404(client):
    response = await client.get("/payments/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Payment not found"}


@pytest.mark.parametrize("payment_id", ["abc", "0", "-1", "99999999999"])
async def test_get_payment_with_invalid_id_is_422(client, payment_id):
    response = await client.get(f"/payments/{payment_id}")

    assert response.status_code == 422
