import hashlib
import hmac

from sqlalchemy import func, select

from app.models import Payment

WEBHOOK_SECRET = "test-webhook-secret"


def payment_body(**overrides):
    """Корректное тело запроса: standard, карта. Тесты меняют только нужные поля."""
    body = {"tariff_id": "standard", "email": "student@example.com", "method": "card"}
    body.update(overrides)
    return body


async def count_payments(session_factory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Payment))


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Подпись вебхука. Намеренно не использует код приложения: тест не должен
    проверять функцию её же собственным результатом."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def post_webhook(client, raw_body: bytes, signature: str | bytes | None):
    """Отправляет вебхук с произвольными байтами и подписью (None - без заголовка).

    Подпись можно передать байтами: httpx не даёт отправить строку с не-ASCII символами.
    """
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Signature"] = signature
    return await client.post("/webhooks/bank", content=raw_body, headers=headers)
