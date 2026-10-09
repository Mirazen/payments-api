import hashlib
import hmac
from typing import Annotated

from fastapi import Header, HTTPException, Request


def compute_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 от сырых байтов тела, шестнадцатеричная строка в нижнем регистре."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def is_valid_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if signature is None:
        return False
    # compare_digest сравнивает за постоянное время (защита от подбора по времени ответа).
    # Сравниваем байты: со строками он падает, если в заголовке не-ASCII символы.
    return hmac.compare_digest(compute_signature(secret, body).encode(), signature.encode())


async def verify_webhook_signature(
    request: Request,
    x_signature: Annotated[str | None, Header()] = None,
) -> None:
    """Зависимость FastAPI: пропускает запрос только с верной подписью, иначе 401."""
    # Подпись считается от тела ровно так, как оно пришло. Если разобрать JSON и
    # собрать заново, пробелы и порядок ключей изменятся, и подпись не совпадёт.
    raw_body = await request.body()
    if not is_valid_signature(request.app.state.webhook_secret, raw_body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
