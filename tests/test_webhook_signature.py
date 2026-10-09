import json

import pytest

from app.config import get_webhook_secret
from app.security import compute_signature
from tests.helpers import payment_body, post_webhook, sign

# --- алгоритм подписи ---


def test_signature_matches_known_hmac_sha256_value():
    # Значение посчитано независимо (hmac.new(b"secret", body, sha256).hexdigest()).
    # Защищает от ошибки в алгоритме: например, SHA1 вместо SHA256.
    body = b'{"payment_id": 1, "status": "succeeded"}'
    expected = "a62537b60971d102c966531d987225da2a41c30cacc506dd03020dfcfb15dc44"

    assert compute_signature("secret", body) == expected
    assert sign(body, "secret") == expected


# --- запрос с подписью ---


async def create_payment(client) -> int:
    return (await client.post("/payments", json=payment_body())).json()["id"]


def webhook_body(payment_id: int, status: str = "succeeded") -> bytes:
    return json.dumps({"payment_id": payment_id, "status": status}).encode()


async def get_status(client, payment_id: int) -> str:
    return (await client.get(f"/payments/{payment_id}")).json()["status"]


async def test_valid_signature_is_accepted(client):
    payment_id = await create_payment(client)
    raw_body = webhook_body(payment_id)

    response = await post_webhook(client, raw_body, sign(raw_body))

    assert response.status_code == 200
    assert response.json() == {"result": "ok"}
    assert await get_status(client, payment_id) == "succeeded"


async def test_missing_signature_is_401_and_status_is_unchanged(client):
    payment_id = await create_payment(client)

    response = await post_webhook(client, webhook_body(payment_id), None)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid signature"}
    assert await get_status(client, payment_id) == "pending"


@pytest.mark.parametrize(
    "signature",
    ["", "abc", "0" * 64, "не-ascii-подпись".encode()],  # не-ASCII не должно давать 500
)
async def test_garbage_signature_is_401_and_status_is_unchanged(client, signature):
    payment_id = await create_payment(client)
    raw_body = webhook_body(payment_id)

    response = await post_webhook(client, raw_body, signature)

    assert response.status_code == 401
    assert await get_status(client, payment_id) == "pending"


async def test_signature_with_wrong_secret_is_401(client):
    payment_id = await create_payment(client)
    raw_body = webhook_body(payment_id)

    response = await post_webhook(client, raw_body, sign(raw_body, secret="other-secret"))

    assert response.status_code == 401
    assert await get_status(client, payment_id) == "pending"


async def test_signature_of_other_body_is_401(client):
    """Подмена тела: подпись от "failed" не подходит к телу "succeeded"."""
    payment_id = await create_payment(client)
    signature_of_failed = sign(webhook_body(payment_id, "failed"))

    response = await post_webhook(
        client, webhook_body(payment_id, "succeeded"), signature_of_failed
    )

    assert response.status_code == 401
    assert await get_status(client, payment_id) == "pending"


async def test_signature_is_checked_over_raw_bytes(client):
    """Тот же JSON по смыслу, но с другими пробелами: подпись считается от байтов."""
    payment_id = await create_payment(client)
    compact = f'{{"payment_id":{payment_id},"status":"succeeded"}}'.encode()
    spaced = f'{{ "payment_id": {payment_id},  "status": "succeeded" }}'.encode()

    wrong = await post_webhook(client, spaced, sign(compact))
    assert wrong.status_code == 401
    assert await get_status(client, payment_id) == "pending"

    right = await post_webhook(client, spaced, sign(spaced))
    assert right.status_code == 200
    assert await get_status(client, payment_id) == "succeeded"


# --- порядок проверок: подпись раньше всего остального ---


async def test_bad_signature_wins_over_missing_payment(client):
    response = await post_webhook(client, webhook_body(999), "bad")

    assert response.status_code == 401  # а не 404: чужой не узнает, какие платежи существуют


async def test_bad_signature_wins_over_invalid_body(client):
    raw_body = json.dumps({"payment_id": 1, "status": "paid"}).encode()

    response = await post_webhook(client, raw_body, "bad")

    assert response.status_code == 401  # а не 422


async def test_bad_signature_wins_over_forbidden_transition(client):
    payment_id = await create_payment(client)
    raw_body = webhook_body(payment_id, "refunded")  # pending -> refunded запрещён

    response = await post_webhook(client, raw_body, "bad")

    assert response.status_code == 401  # а не 409


async def test_valid_signature_with_missing_payment_is_404(client):
    raw_body = webhook_body(999)

    response = await post_webhook(client, raw_body, sign(raw_body))

    assert response.status_code == 404


async def test_other_endpoints_do_not_require_signature(client):
    assert (await client.get("/tariffs")).status_code == 200
    assert (await client.post("/payments", json=payment_body())).status_code == 201


# --- секрет обязателен ---


def test_missing_secret_stops_the_app(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        get_webhook_secret()


def test_empty_secret_stops_the_app(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "")

    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        get_webhook_secret()


def test_secret_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "from-env")

    assert get_webhook_secret() == "from-env"
